import { createClient } from "@supabase/supabase-js";
import { createPatch } from "diff";

const supabase = createClient(
  import.meta.env.VITE_SUPABASE_URL,
  import.meta.env.VITE_SUPABASE_ANON_KEY
);

const app = document.getElementById("app");

function versionIdFromPath() {
  // /r/<uuid>, matching web/vercel.json's rewrite
  const match = window.location.pathname.match(/\/r\/([0-9a-f-]{36})/i);
  return match ? match[1] : null;
}

function escapeHtml(s) {
  return (s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function publicFileUrl(storagePath) {
  const base = import.meta.env.VITE_SUPABASE_URL.replace(/\/$/, "");
  return `${base}/storage/v1/object/public/document-snapshots/${storagePath}`;
}

function renderError(message) {
  app.innerHTML = `<h1>Receipt not found</h1><p class="error">${escapeHtml(message)}</p>`;
}

function unifiedDiffText(oldText, newText) {
  // The diff itself is never stored -- it's fully reproducible from the two
  // (immutable, already-stored) text_extracted values, so recomputing it
  // here avoids a redundant, potentially-stale copy of the same data.
  const patch = createPatch("document", oldText, newText, "previous", "current");
  const lines = patch.split("\n");
  const start = lines.findIndex((l) => l.startsWith("---"));
  return start === -1 ? "" : lines.slice(start).join("\n");
}

function renderDiff(diffText) {
  if (!diffText) return "<p class=\"meta\">No diff available.</p>";
  const lines = diffText.split("\n").map((line) => {
    const cls = line.startsWith("+") && !line.startsWith("+++") ? "diff-line-add"
      : line.startsWith("-") && !line.startsWith("---") ? "diff-line-del"
      : "";
    return `<span class="${cls}">${escapeHtml(line)}</span>`;
  });
  return `<div class="scroll-box"><pre>${lines.join("\n")}</pre></div>`;
}

async function loadReceipt(versionId) {
  const { data: version, error: versionErr } = await supabase
    .from("document_versions")
    .select("id, document_id, sha256, storage_path, captured_at, text_extracted, diff_summary, prev_version_id")
    .eq("id", versionId)
    .single();

  if (versionErr || !version) {
    renderError("No document version exists with this id. The link may be malformed, or this environment hasn't been granted read access yet.");
    return;
  }

  const { data: doc } = await supabase
    .from("documents")
    .select("id, doc_type, source_url, meeting_id")
    .eq("id", version.document_id)
    .single();

  const { data: meeting } = await supabase
    .from("meetings")
    .select("id, meeting_date, source_url, body_id")
    .eq("id", doc.meeting_id)
    .single();

  const { data: body } = await supabase
    .from("bodies")
    .select("name, jurisdiction")
    .eq("id", meeting.body_id)
    .single();

  let prevDiff = null;
  if (version.prev_version_id) {
    const { data: prev } = await supabase
      .from("document_versions")
      .select("text_extracted")
      .eq("id", version.prev_version_id)
      .single();
    if (prev) {
      prevDiff = unifiedDiffText(prev.text_extracted || "", version.text_extracted || "");
    }
  }

  render({ version, doc, meeting, body, prevDiff });
}

function render({ version, doc, meeting, body, prevDiff }) {
  const capturedAt = new Date(version.captured_at).toUTCString();

  app.innerHTML = `
    <h1>${escapeHtml(body.name)}</h1>
    <div class="meta">
      ${escapeHtml(body.jurisdiction)} &middot;
      Meeting ${escapeHtml(meeting.meeting_date)} &middot;
      ${escapeHtml(doc.doc_type)}
    </div>

    <div class="card">
      <h2>Verification</h2>
      <p><strong>SHA-256</strong><br /><span class="hash">${escapeHtml(version.sha256)}</span></p>
      <p><strong>Captured</strong><br />${escapeHtml(capturedAt)} UTC</p>
      <a class="download" href="${publicFileUrl(version.storage_path)}" target="_blank" rel="noopener">
        Download original file
      </a>
    </div>

    ${version.prev_version_id ? `
    <div class="card">
      <h2>What changed</h2>
      <p class="summary">${escapeHtml(version.diff_summary || "")}</p>
      ${renderDiff(prevDiff)}
    </div>` : `
    <div class="card">
      <h2>What changed</h2>
      <p class="meta">First captured version of this document — no prior version to compare.</p>
    </div>`}

    <div class="card">
      <h2>Current text</h2>
      <div class="text-block">${escapeHtml(version.text_extracted || "(no text could be extracted from this file)")}</div>
    </div>
  `;
}

const versionId = versionIdFromPath();
if (!versionId) {
  renderError("No document version id in the URL. Expected /r/<uuid>.");
} else {
  loadReceipt(versionId).catch((err) => renderError(String(err)));
}
