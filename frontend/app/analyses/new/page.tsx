"use client";

import Link from "next/link";
import { useState } from "react";

import { EmptyBlock, ErrorBlock, Loading, useAsync } from "@/components/AsyncState";
import { ApiError, normalizeError } from "@/lib/api/errors";
import { formatDateTime } from "@/lib/api/format";
import { createAnalysis, listProfiles } from "@/lib/api/resources";
import type { Analysis, ProfileSummary } from "@/lib/api/types";

interface ProfileOptions {
  resume: ProfileSummary[];
  jd: ProfileSummary[];
}

export default function NewAnalysisPage() {
  const profiles = useAsync<ProfileOptions>("new:profiles", async () => {
    const [resume, jd] = await Promise.all([
      listProfiles("resume", { limit: 200 }),
      listProfiles("jd", { limit: 200 }),
    ]);
    return { resume: resume.items, jd: jd.items };
  });

  const [resumeId, setResumeId] = useState("");
  const [jdId, setJdId] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [created, setCreated] = useState<Analysis | null>(null);
  const [submitError, setSubmitError] = useState<ApiError | null>(null);

  const data = profiles.state.status === "ready" ? profiles.state.data : null;
  const resumeProfiles = data?.resume ?? [];
  const jdProfiles = data?.jd ?? [];
  const selectedResume = resumeProfiles.find((item) => item.profile_id === resumeId) ?? resumeProfiles[0] ?? null;
  const selectedJd = jdProfiles.find((item) => item.profile_id === jdId) ?? jdProfiles[0] ?? null;

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitError(null);
    setCreated(null);
    if (!selectedResume || !selectedJd) {
      setSubmitError(
        new ApiError({ code: "VALIDATION_FAILED", message: "请先选择 resume 与 jd profile。" }),
      );
      return;
    }
    setSubmitting(true);
    try {
      const analysis = await createAnalysis({
        resume_document_id: selectedResume.document_id,
        jd_document_id: selectedJd.document_id,
        resume_profile_id: selectedResume.profile_id,
        jd_profile_id: selectedJd.profile_id,
      });
      setCreated(analysis);
    } catch (error) {
      setSubmitError(normalizeError(error));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div data-testid="create-analysis">
      <div className="page-header">
        <h1>新建分析</h1>
      </div>

      {profiles.state.status === "loading" ? <Loading /> : null}
      {profiles.state.status === "error" ? (
        <ErrorBlock error={profiles.state.error} onRetry={profiles.reload} />
      ) : null}

      {data && (resumeProfiles.length === 0 || jdProfiles.length === 0) ? (
        <EmptyBlock message="缺少可用 profile：请先通过 API 完成一次抽取（resume / jd 各至少一个 profile）。" />
      ) : null}

      {data && resumeProfiles.length > 0 && jdProfiles.length > 0 ? (
        <form onSubmit={handleSubmit}>
          <div className="field">
            <label htmlFor="resume-select">resume profile</label>
            <select
              id="resume-select"
              data-testid="resume-select"
              value={selectedResume?.profile_id ?? ""}
              onChange={(event) => setResumeId(event.target.value)}
            >
              {resumeProfiles.map((profile) => (
                <option key={profile.profile_id} value={profile.profile_id}>
                  {profile.reference} · {formatDateTime(profile.extracted_at)} · warnings={profile.warning_count}
                </option>
              ))}
            </select>
          </div>

          <div className="field">
            <label htmlFor="jd-select">jd profile</label>
            <select
              id="jd-select"
              data-testid="jd-select"
              value={selectedJd?.profile_id ?? ""}
              onChange={(event) => setJdId(event.target.value)}
            >
              {jdProfiles.map((profile) => (
                <option key={profile.profile_id} value={profile.profile_id}>
                  {profile.reference} · {formatDateTime(profile.extracted_at)} · warnings={profile.warning_count}
                </option>
              ))}
            </select>
          </div>

          <button type="submit" data-testid="create-submit" disabled={submitting}>
            {submitting ? "创建中…" : "创建分析"}
          </button>
        </form>
      ) : null}

      <div aria-live="polite">
        {submitError ? <ErrorBlock error={submitError} /> : null}

        {created ? (
          <div className="created-box" data-testid="created-analysis">
            <p>
              analysis id: <code data-testid="created-analysis-id">{created.id}</code>
            </p>
            <p>
              status: <strong data-testid="created-analysis-status">{created.status}</strong>
              {created.reused ? "（复用已有分析）" : ""}
            </p>
            <p>
              <Link href={`/analyses/${created.id}`}>进入分析详情</Link>
            </p>
          </div>
        ) : null}
      </div>
    </div>
  );
}
