# §4.5 Part A：tool result 溢写异步化 + 原子化 Design

> **状态**：已 brainstorming + 用户确认（只做 Part A；Part B token 增量缓存 defer），待 writing-plans 出实施计划。
> **对应**：`docs/superpowers/specs/2026-06-22-性能优化-design.md` §4.5（仅 Part A）；总基准见 `docs/PROGRESS_BASELINE.md` §6。
> **分支**：`feature/usb-portable`（HEAD `b3edecb4`）。
> **行号基准**：本文 `file:line` 截至 HEAD `b3edecb4`；改动后会漂移（PROGRESS_BASELINE §8 坑-6），写实施计划时用 grep 重新定位。

---

## 1. 目标与范围

**目标**：把 `light_context_manager.py` 里**溢写超大 tool result 到 `.txt` 文件**的同步 `fp.write_text` 改为 `asyncio.to_thread(write_bytes_atomic)`——离事件循环 + 原子（tmp+fsync+replace+per-path 锁）。每个返回大结果的 tool 调用（文件读取、网页抓取等）不再阻塞事件循环，且 exFAT 崩溃无半文件。

**In scope（本 spec）**：
- `LightContextManager` 的 `_truncate_tool_result` / `_prune_output` / `_prune_tool_result` 三函数 async 化（受控线性传播）。
- 溢写改 `write_bytes_atomic`（经 `to_thread`）。

**Out of scope（明确不做）**：
- **Part B（token 计数增量缓存）defer**：`pre_reasoning` 每轮全历史重算的成本主要在 `stat_message` 重复格式化旧消息，但 `EstimatedTokenCounter` 是字节除法（计数廉价），收益边际；而增量缓存需在 prune/summary 改消息时正确失效，复杂度不配其低优先级收尾定位。若将来实测长会话 token 计数成瓶颈，再单独评估。
- `post_acting` / `_check_context` / 其它 hook 逻辑不动。
- `truncate_text_output` / 截断 notice 逻辑不动。

---

## 2. 调查事实（设计依据）

### 2.1 溢写点

`_truncate_tool_result`（`light_context_manager.py:133`，**同步**）：当 tool result 内容超 `max_bytes` 时，把全文写到 `tool_result_dir / {uuid}.txt`，返回带 file_path notice 的截断内容。关键写盘（line 177-181）：
```python
try:
    tool_result_dir.mkdir(parents=True, exist_ok=True)
    fp = tool_result_dir / f"{uuid.uuid4().hex}.txt"
    fp.write_text(content, encoding=encoding)   # ← 同步、阻塞事件循环、裸写（无 fsync/原子）
    saved_path = str(fp)
except OSError as e:
    logger.exception(...)
    return truncate_text_output(content, max_bytes=max_bytes, encoding=encoding)
```

### 2.2 调用链（干净线性，两内层无外部调用方）

```
post_acting (:944 async) → _prune_tool_result (:229 async)
   → _prune_output (:201 SYNC, 唯一调用点 :330)
      → _truncate_tool_result (:133 SYNC, 调用点 :218/:222)
         → fp.write_text (:180)   # 阻塞
```
- `_truncate_tool_result` **仅**被 `_prune_output` 调（:218/:222）。
- `_prune_output` **仅**被 `_prune_tool_result` 调（:330）。
- `_prune_tool_result` **仅**被 `post_acting` 调（:960）。
→ async 化是受控的 3 函数传播，无外部调用方需改。

### 2.3 atomic_io API

`write_bytes_atomic(path, data: bytes, *, lock=True, fsync=True)`（`utils/atomic_io.py:50`）：tmp+fsync+`os.replace`+per-path `RLock`；自带 `target.parent.mkdir(parents=True, exist_ok=True)`（故显式 mkdir 冗余）；失败抛 `OSError`、清 tmp、原文件不动。无 `write_text_atomic` → 文本经 `content.encode(encoding)` 入。

### 2.4 现状测试覆盖

`grep` 确认：**无任何** `light_context_manager` / `_truncate_tool_result` / `_prune_tool_result` / `post_acting` 的现存测试。本 spec 需新建测试文件。

### 2.5 构造可行性

`LightContextManager(working_dir: str, agent_id: str)`（:60，轻量构造）。`_truncate_tool_result` 经 `load_agent_config(self.agent_id)` 取 `trc.tool_results_cache`——测试可 monkeypatch `load_agent_config` 返回含该字段的 stub，无需真 agent 配置。

---

## 3. 设计：3 函数 async 传播 + 原子溢写

### 3.1 imports

`light_context_manager.py` 当前无 `asyncio`、无 `atomic_io`。加：
```python
import asyncio
```
（顶部 stdlib 区）与
```python
from ...utils.atomic_io import write_bytes_atomic
```
（相对 `agents/context/` → 3 点到 `qwenpaw/utils/`）。

### 3.2 `_truncate_tool_result`：sync → async，写改 `to_thread(write_bytes_atomic)`

`def` → `async def`。把 line 177-181 的写块（mkdir + write_text）换为：
```python
    try:
        fp = tool_result_dir / f"{uuid.uuid4().hex}.txt"
        await asyncio.to_thread(
            write_bytes_atomic,
            fp,
            content.encode(encoding),
        )
        saved_path = str(fp)
    except OSError as e:
        logger.exception(f"Failed to save tool result to file: {e}")
        return truncate_text_output(
            content,
            max_bytes=max_bytes,
            encoding=encoding,
        )
```
（`write_bytes_atomic` 自带 parent mkdir → 显式 `tool_result_dir.mkdir(...)` 删除。`content.encode(encoding)` 在事件循环算——CPU、廉价；写盘在 thread。）

### 3.3 `_prune_output`：sync → async

`def` → `async def`。两处调用改 await：
```python
        if isinstance(output, str):
            return await self._truncate_tool_result(output, max_bytes, encoding)
        if isinstance(output, list):
            for block in output:
                if isinstance(block, dict) and block.get("type") == "text":
                    block["text"] = await self._truncate_tool_result(
                        block.get("text", ""),
                        max_bytes,
                        encoding,
                    )
        return output
```

### 3.4 `_prune_tool_result`：:330 调用改 await

```python
                    block["output"] = await self._prune_output(
```
（仅这一处调用点改 `await`；`_prune_tool_result` 本就是 `async def`。）

---

## 4. 改动清单（全在 `light_context_manager.py`）

| 位置 | 改动 |
|---|---|
| imports | 加 `import asyncio`、`from ...utils.atomic_io import write_bytes_atomic` |
| `_truncate_tool_result` (:133) | `def`→`async def`；写块改 `await asyncio.to_thread(write_bytes_atomic, fp, content.encode(encoding))`；删冗余 mkdir |
| `_prune_output` (:201) | `def`→`async def`；两处 `_truncate_tool_result` 加 `await` |
| `_prune_tool_result` (:330) | `_prune_output(...)` 加 `await` |

不动：`post_acting`、`_check_context`、`truncate_text_output`、notice 逻辑、其余 hook。

---

## 5. 数据流（after）

```
post_acting (async)
  → _prune_tool_result (async) → 遍历 messages
       → await _prune_output (async)  # :330
            → await _truncate_tool_result (async)  # :218/:222
                 → 超限？await asyncio.to_thread(write_bytes_atomic, fp, bytes)  # 离事件循环、原子
                 → 返回带 file_path notice 的截断内容
```
事件循环在溢写期间空闲；文件原子落盘。

---

## 6. 错误处理 / 边界

| 情形 | 行为 |
|---|---|
| 内容 ≤ max_bytes | 不溢写，返回原内容（直通，未变） |
| `write_bytes_atomic` 抛 OSError | 现有 `except OSError` 接住 → 回退 `truncate_text_output`（不带 file_path）——**与今天一致** |
| 原子写成功 | tmp+fsync+replace → 无半文件、无 `.tmp` 残留（C1 exFAT 健壮） |
| 目录不存在 | `write_bytes_atomic` 自带 `parent.mkdir` → 创建 |
| 未超限的 list[dict] / str | `_prune_output` 两条路径都走 `await _truncate_tool_result`（命中即 await；不命中直通） |

---

## 7. 测试（新建 `tests/unit/agents/context/test_tool_result_offload.py`）

Fixture：`LightContextManager(working_dir=str(tmp_path), agent_id="test-agent")` + `monkeypatch.setattr("qwenpaw.agents.context.light_context_manager.load_agent_config", lambda aid: <stub>)`，stub 含 `running.light_context_config.tool_result_pruning_config.tool_results_cache = "tool_results"`。

1. `test_overmax_writes_file_atomically` — 超限内容 → `tmp_path/tool_results/*.txt` 存在、内容 == 原文、无 `.tmp.*` 残留、返回内容含 notice + 该 file_path。
2. `test_undermax_no_file` — 未超限 → 无文件写出、返回原内容。
3. `test_write_offloaded_to_thread` — monkeypatch `asyncio.to_thread` spy，断言溢写时被调用且 func 为 `write_bytes_atomic`。
4. `test_write_failure_falls_back_without_filepath` — patch `write_bytes_atomic` 抛 OSError → 返回截断内容**不含** file_path（回退路径）。
5. `test_prune_output_str_and_list_paths` — `_prune_output` 对 str 与 list[dict]（含 text block）两条路径都正确 await + 截断。
6. `test_prune_tool_result_integration` — 构造含一个超限 tool_result block 的 messages，`await _prune_tool_result(...)` → block 被截断、对应 `.txt` 写出。

回归：因无现存 light_context 测试，无直接回归；但确保 `truncate_text_output` / notice marker 行为不变（测试 1/4 间接覆盖）。

---

## 8. C1–C5 / 风险

| 约束 | 合规 |
|---|---|
| C1 USB/exFAT | 每个 big tool 调用的同步裸写 → 离事件循环原子写（fsync+replace）；tool 密集 agent 实惠，exFAT 无半文件 |
| C2 单实例进程内 | `write_bytes_atomic` 内 per-path `RLock`；无跨进程 |
| C3 小幅行为变更 | 写异步 + 原子（语义同：文件最终有内容、notice 指向它）；OSError 回退逐字保留 |

**风险（低）**：
1. **3 函数改 async**：受控线性传播，两内层无外部调用方（§2.2 已 grep 确认）。唯一需注意的是 `_prune_output` / `_truncate_tool_result` 的所有调用点都改了 await（共 3 处）。
2. **无现存测试**：新建 fixture（monkeypatch `load_agent_config`），计划阶段敲定 stub 的精确字段路径。
3. **`content.encode(encoding)` 在事件循环**：CPU 廉价；若实测超大内容编码也成瓶颈，可移入 `to_thread`（本次不做，YAGNI）。

---

## 9. Part B（token 增量缓存）为何 defer

- `EstimatedTokenCounter.count`（`utils/estimate_token_counter.py:39`）= `int(len(text.encode)/divisor + 0.5)`，纯字节除法，**计数本身极廉价**。
- `pre_reasoning` 每轮重算的真实成本是 `stat_message` **重复格式化**旧消息（`count_msgs_token` 循环），主要长会话受益。
- 增量缓存需按 (列表长度 + 末尾指纹) 计增量，且 prune（`post_acting` 截断 tool result）/ summary（压缩）改消息内容时**必须正确失效**，否则 compact 阈值判断错 → 上下文压缩异常。
- 失效正确性的复杂度 ↔ 边际收益（长会话省格式化）不配 §4.5 的"低优先级收尾"定位。
- **结论**：defer。若将来 `-X importtime`/profiling 实测长会话 token 计数成瓶颈，再出独立 spec（指纹缓存 + prune/summary 失效钩子）。

---

## 10. 后续

- 本 spec 经用户过目 → `superpowers:writing-plans` 出 bite-sized TDD 实施计划（每 Task 先红后绿 + before→after 完整代码 + commit）。
- 完成后回写 `PROGRESS_BASELINE.md`：§5 加 Phase 4d（Part A）、§6 的 §4.5 行改为"Part A 完成 / Part B defer"、§0 改现状。
