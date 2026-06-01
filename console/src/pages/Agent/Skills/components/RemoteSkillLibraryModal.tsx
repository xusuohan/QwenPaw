import { Button, Modal, Tooltip } from "@agentscope-ai/design";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { DownloadOutlined, ExportOutlined, LinkOutlined } from "@ant-design/icons";
import styles from "./RemoteSkillLibraryModal.module.less";
import { getApiUrl } from "../../../../api/config";
import { buildAuthHeaders } from "../../../../api/authHeaders";
import { useAppMessage } from "../../../../hooks/useAppMessage";

interface RemoteSkillLibraryModalProps {
  open: boolean;
  onCancel: () => void;
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

export function RemoteSkillLibraryModal({
  open,
  onCancel,
}: RemoteSkillLibraryModalProps) {
  const { t } = useTranslation();
  const { message } = useAppMessage();
  const [realUrl, setRealUrl] = useState(SKILLHUB_HOME);
  const [downloadUrl, setDownloadUrl] = useState<string | null>(null);
  const [downloadName, setDownloadName] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);

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
      }
    };

    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [open, resetState]);

  const handleDownload = useCallback(async () => {
    if (!downloadUrl) return;
    if (downloading) return;

    const target_name = downloadName?.trim() || "download";
    setDownloading(true);
    try {
      const resp = await fetch(getApiUrl("/skills/download"), {
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

      const contentType = resp.headers.get("content-type") || "";

      // Most likely: backend returns the zip as a binary attachment.
      const looksLikeZip =
        contentType.includes("application/zip") ||
        contentType.includes("application/octet-stream") ||
        (resp.headers.get("content-disposition") || "").includes("attachment");

      if (looksLikeZip) {
        const blob = await resp.blob();
        const url = window.URL.createObjectURL(blob);

        // Prefer filename from skillhub route `name=`; fallback to backend headers; then to target_name.
        const preferredName = downloadName?.trim();
        let filename = preferredName || target_name;
        if (!preferredName) {
          const cd = resp.headers.get("content-disposition") || "";
          const filenameMatch = cd.match(
            /filename\*?=(?:UTF-8'')?["']?([^"';\n]+)["']?/i,
          );
          if (filenameMatch?.[1]) {
            filename = decodeURIComponent(filenameMatch[1]);
          }
        }

        const a = document.createElement("a");
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.URL.revokeObjectURL(url);
        return;
      }

      // Fallback: some APIs return JSON containing a downloadable URL.
      const data = await resp.json().catch(() => null);
      const downloadLink =
        data?.url || data?.download_url || data?.downloadUrl || null;
      if (typeof downloadLink === "string" && downloadLink) {
        window.open(downloadLink, "_blank", "noopener,noreferrer");
        return;
      }

      // Async backend mode: request accepted and processed server-side.
      message.success(t("common.success"));
    } catch (err) {
      message.error(err instanceof Error ? err.message : String(err));
    } finally {
      setDownloading(false);
    }
  }, [downloading, downloadUrl, downloadName, message, t]);

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
            icon={<DownloadOutlined />}
            disabled={!downloadUrl || downloading}
            onClick={handleDownload}
          >
            {t("common.download")}
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
