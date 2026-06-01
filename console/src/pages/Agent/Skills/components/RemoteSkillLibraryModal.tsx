import { Button, Modal, Tooltip } from "@agentscope-ai/design";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { DownloadOutlined, ExportOutlined, LinkOutlined } from "@ant-design/icons";
import styles from "./RemoteSkillLibraryModal.module.less";

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

function parseDownloadUrlFromRoute(route: string): string | null {
  if (!route.includes("download")) return null;
  try {
    const parsed = new URL(route, SKILLHUB_ORIGIN);
    const download = parsed.searchParams.get("download");
    if (!download) return null;
    return download.startsWith("http://") || download.startsWith("https://")
      ? download
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
  const [realUrl, setRealUrl] = useState(SKILLHUB_HOME);
  const [downloadUrl, setDownloadUrl] = useState<string | null>(null);

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
        setDownloadUrl(parseDownloadUrlFromRoute(data.route));
      }
    };

    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [open, resetState]);

  const handleDownload = () => {
    if (!downloadUrl) return;
    // Direct file URL from SkillHub — open like entering it in the address bar.
    window.open(downloadUrl, "_blank", "noopener,noreferrer");
  };

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
            disabled={!downloadUrl}
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
