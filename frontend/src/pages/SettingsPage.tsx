import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError, getHchatSettings, updateHchatSettings } from "../api/client";

type Notice = { kind: "success" | "error"; text: string };

export function SettingsPage() {
  const queryClient = useQueryClient();
  const [apiKey, setApiKey] = useState("");
  const [notice, setNotice] = useState<Notice | null>(null);

  useEffect(() => {
    document.title = "설정 · Price Analyzer";
    return () => {
      document.title = "Price Analyzer";
    };
  }, []);

  const hchatSettings = useQuery({
    queryKey: ["hchat-settings"],
    queryFn: ({ signal }) => getHchatSettings(signal),
  });

  const saveKey = useMutation({
    mutationFn: (value: string) => updateHchatSettings(value),
    onSuccess: () => {
      setApiKey("");
      setNotice({ kind: "success", text: "hChat 키를 저장했습니다." });
      void queryClient.invalidateQueries({ queryKey: ["hchat-settings"] });
    },
    onError: (error: unknown) => {
      setNotice({
        kind: "error",
        text:
          error instanceof ApiError
            ? error.message
            : "hChat 키 저장에 실패했습니다.",
      });
    },
  });

  return (
    <main className="workspace-page">
      <header className="page-heading">
        <div>
          <p className="section-kicker">환경 설정</p>
          <h1>설정</h1>
        </div>
        <p>배포받은 환경마다 필요한 개인 API 키를 등록합니다.</p>
      </header>

      {notice && (
        <div
          className={`workspace-notice is-${notice.kind}`}
          role={notice.kind === "error" ? "alert" : "status"}
        >
          {notice.text}
        </div>
      )}

      {hchatSettings.isPending && (
        <p className="inline-state shimmer-text" role="status">
          설정을 불러오는 중…
        </p>
      )}
      {hchatSettings.isError && (
        <p className="inline-state is-error">설정을 불러오지 못했습니다.</p>
      )}
      {hchatSettings.data && !hchatSettings.data.enabled && (
        <p className="inline-state">관리자가 이 기능을 활성화하지 않았습니다.</p>
      )}
      {hchatSettings.data && hchatSettings.data.enabled && (
        <section className="settings-panel" aria-labelledby="hchat-settings-title">
          <div className="intake-title">
            <span>01</span>
            <div>
              <h2 id="hchat-settings-title">hChat 임베딩 키</h2>
              <p>품목 매칭 정확도를 높이는 데 사용됩니다.</p>
            </div>
            <span
              className={`market-state is-${hchatSettings.data.has_key ? "live" : "unavailable"}`}
            >
              {hchatSettings.data.has_key ? "설정됨" : "미설정"}
            </span>
          </div>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              setNotice(null);
              saveKey.mutate(apiKey);
            }}
          >
            <label className="submitter-field">
              <span>API 키</span>
              <input
                id="hchat-api-key"
                aria-label="hChat API 키"
                type="password"
                value={apiKey}
                disabled={saveKey.isPending}
                placeholder={
                  hchatSettings.data.has_key
                    ? "•••••••• (저장됨, 변경하려면 새 값 입력)"
                    : "API 키 입력"
                }
                onChange={(event) => setApiKey(event.target.value)}
              />
              <small className="submitter-helper">
                이 값은 이 컴퓨터의 로컬 DB에만 저장되며 Git에 포함되지 않습니다.
              </small>
            </label>
            <button
              className="primary-action stable-action"
              type="submit"
              disabled={saveKey.isPending}
            >
              {saveKey.isPending ? (
                <span className="shimmer-text">저장 중…</span>
              ) : (
                "저장"
              )}
            </button>
          </form>
        </section>
      )}
    </main>
  );
}
