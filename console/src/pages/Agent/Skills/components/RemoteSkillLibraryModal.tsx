import { Button, Modal, Tooltip } from "@agentscope-ai/design";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { DownloadOutlined, ExportOutlined, LinkOutlined, SyncOutlined } from "@ant-design/icons";
import styles from "./RemoteSkillLibraryModal.module.less";
import { getApiUrl } from "../../../../api/config";
import { buildAuthHeaders } from "../../../../api/authHeaders";
import { useAppMessage } from "../../../../hooks/useAppMessage";

interface RemoteSkillLibraryModalProps {
  open: boolean;
  onCancel: () => void;
  onDownloadSuccess?: () => void | Promise<void>;
  poolMode?: boolean;
}

const SKILLHUB_ORIGIN = "https://skillhub.goldlokai.com";
const SKILLHUB_HOME = `${SKILLHUB_ORIGIN}/`;

type SkillhubMessage = {
  type?: string;
  route?: string;
  url?: string;
  href?: string;
};

function parseDownloadUrlFromRoute(
  route: string,
): { downloadUrl: string; downloadName: string | null } | null {
  if (!route.includes("download")) return null;
  try {
    const parsed = new URL(route, SKILLHUB_ORIGIN);
    const download = parsed.searchParams.get("download");
    const downloadName = parsed.searchParams.get("name");
    if (!download) return null;
    return download.startsWith("http://") || download.startsWith("https://")
      ? {
          downloadUrl: download,
          downloadName: downloadName?.trim() || null,
        }
      : null;
  } catch {
    return null;
  }
}

function resolveDisplayUrl(data: SkillhubMessage): string | null {
  if (data.type === "skillhub:navigate" && typeof data.url === "string") {
    return data.url;
  }
  if (data.type === "skillhub:location" && typeof data.href === "string") {
    return data.href;
  }
  if (typeof data.route === "string") {
    try {
      return new URL(data.route, SKILLHUB_ORIGIN).toString();
    } catch {
      return null;
    }
  }
  return null;
}

async function checkSkillExists(skillName: string, endpoint: string): Promise<boolean> {
  try {
    const resp = await fetch(getApiUrl(endpoint), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...buildAuthHeaders(),
      },
      body: JSON.stringify({ skill_name: skillName }),
    });
    if (!resp.ok) return false;
    const data = await resp.json();
    return data.exists === true;
  } catch {
    return false;
  }
}

export function RemoteSkillLibraryModal({
  open,
  onCancel,
  onDownloadSuccess,
  poolMode = false,
}: RemoteSkillLibraryModalProps) {
  const { t } = useTranslation();
  const { message } = useAppMessage();
  const [realUrl, setRealUrl] = useState(SKILLHUB_HOME);
  const [downloadUrl, setDownloadUrl] = useState<string | null>(null);
  const [downloadName, setDownloadName] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [skillExists, setSkillExists] = useState<boolean | null>(null);
  const [checking, setChecking] = useState(false);

  const downloadEndpoint = poolMode ? "/pool/skills/download" : "/skills/download";
  const checkEndpoint = poolMode ? "/pool/skills/check" : "/skills/check";

  const displayUrl = useMemo(() => {
    try {
      return new URL(realUrl).toString();
    } catch {
      return SKILLHUB_HOME;
    }
  }, [realUrl]);

  const resetState = useCallback(() => {
    setRealUrl(SKILLHUB_HOME);
    setDownloadUrl(null);
    setDownloadName(null);
    setSkillExists(null);
    setChecking(false);
  }, []);

  useEffect(() => {
    if (!open) {
      resetState();
      return;
    }

    const onMessage = (event: MessageEvent) => {
      if (event.origin !== SKILLHUB_ORIGIN) return;
      const data = event.data as SkillhubMessage | undefined;
      if (!data || typeof data !== "object") return;

      const nextDisplay = resolveDisplayUrl(data);
      if (nextDisplay) setRealUrl(nextDisplay);

      if (data.type === "skillhub:route-change" && typeof data.route === "string") {
        const parsed = parseDownloadUrlFromRoute(data.route);
        setDownloadUrl(parsed?.downloadUrl ?? null);
        setDownloadName(parsed?.downloadName ?? null);
        setSkillExists(null);

        // Check if skill exists when downloadName is available
        if (parsed?.downloadName) {
          setChecking(true);
          checkSkillExists(parsed.downloadName, checkEndpoint)
            .then(setSkillExists)
            .finally(() => setChecking(false));
        }
      }
    };

    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [open, resetState, checkEndpoint]);

  const handleDownload = useCallback(async () => {
    if (!downloadUrl) return;
    if (downloading) return;

    const target_name = downloadName?.trim() || "download";
    setDownloading(true);
    try {
      const resp = await fetch(getApiUrl(downloadEndpoint), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...buildAuthHeaders(),
        },
        body: JSON.stringify({
          zip_url: downloadUrl,
          enable: true,
          target_name,
        }),
      });

      if (!resp.ok) {
        const text = await resp.text().catch(() => "");
        throw new Error(text || `Download request failed: ${resp.status}`);
      }

      const data = await resp.json();
      if (data.success) {
        message.success(t("skills.remoteSkillLibraryDownloadSuccess"));
        setSkillExists(true);
        await onDownloadSuccess?.();
      }
    } catch (err) {
      message.error(err instanceof Error ? err.message : String(err));
    } finally {
      setDownloading(false);
    }
  }, [downloading, downloadUrl, downloadName, message, onDownloadSuccess, t, downloadEndpoint]);

  const isOverwrite = skillExists === true;
  const buttonDisabled = !downloadUrl || downloading || checking;

  return (
    <Modal
      className={styles.remoteSkillLibraryModal}
      title={t("skills.remoteSkillLibrary")}
      open={open}
      onCancel={onCancel}
      width={1200}
      destroyOnHidden
      centered
      footer={
        <div className={styles.modalFooter}>
          <Button onClick={onCancel}>{t("common.cancel")}</Button>
          <Button
            type="primary"
            icon={isOverwrite ? <SyncOutlined /> : <DownloadOutlined />}
            disabled={buttonDisabled}
            onClick={handleDownload}
          >
            {isOverwrite ? t("common.overwrite") : t("common.download")}
          </Button>
        </div>
      }
    >
      <div className={styles.modalBody}>
        <div className={styles.urlBar}>
          <LinkOutlined className={styles.urlIcon} />
          <Tooltip title={displayUrl}>
            <span className={styles.urlText}>{displayUrl}</span>
          </Tooltip>
          <Button
            type="text"
            icon={<ExportOutlined />}
            onClick={() =>
              window.open(displayUrl, "_blank", "noopener,noreferrer")
            }
          />
        </div>
        <div className={styles.frameWrapper}>
          <iframe
            src={SKILLHUB_HOME}
            title={t("skills.remoteSkillLibrary")}
            className={styles.frame}
            loading="lazy"
            referrerPolicy="no-referrer-when-downgrade"
          />
        </div>
      </div>
    </Modal>
  );
}
