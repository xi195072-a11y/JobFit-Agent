"use client";

import {
  formatScore,
  skillStatusClass,
  skillStatusDescription,
  skillStatusLabel,
  truncateId,
} from "@/lib/api/format";
import type { SkillMatch } from "@/lib/api/types";

export function SkillsTable({ skills }: { skills: SkillMatch[] }) {
  if (skills.length === 0) {
    return <p className="muted">无技能匹配结果。</p>;
  }

  return (
    <table className="data-table" data-testid="skills-table">
      <caption className="sr-only">技能匹配结果</caption>
      <thead>
        <tr>
          <th scope="col">requirement</th>
          <th scope="col">candidate</th>
          <th scope="col">status</th>
          <th scope="col">norm_used</th>
          <th scope="col">score_contribution</th>
          <th scope="col">evidence</th>
        </tr>
      </thead>
      <tbody>
        {skills.map((skill) => (
          <tr key={skill.jd_requirement_id} data-testid="skill-row">
            <td>
              <code>{truncateId(skill.jd_requirement_id)}</code>
            </td>
            <td>
              {skill.resume_skill_id ? (
                <code>{truncateId(skill.resume_skill_id)}</code>
              ) : (
                <span className="muted">无</span>
              )}
            </td>
            <td className={skillStatusClass(skill.status)} title={skillStatusDescription(skill.status)}>
              {skillStatusLabel(skill.status)}
            </td>
            <td>{skill.norm_used ?? "—"}</td>
            <td>{formatScore(skill.score_contribution)}</td>
            <td>
              {skill.evidence_ids.length === 0 ? (
                <span className="muted">0</span>
              ) : (
                <span>
                  {skill.evidence_ids.length} ·{" "}
                  <code>{skill.evidence_ids.map((id) => truncateId(id, 6)).join(", ")}</code>
                </span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
