"""session.py — Session index card (session.yaml) read/write/list/status."""
from pathlib import Path
import datetime, json, uuid, yaml

def _generate_session_id(prefix="code"):
    """Generate a stable session id: code-YYYYMMDD-shortuuid."""
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d")
    short = uuid.uuid4().hex[:6]
    return f"{prefix}-{ts}-{short}"




def _git_info(workspace):
    """Return (origin_url, commit) for a git repository, or empty strings."""
    import subprocess

    def run(args):
        result = subprocess.run(
            ["git", "-C", str(workspace), *args],
            capture_output=True, text=True, check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    return run(["config", "--get", "remote.origin.url"]), run(["rev-parse", "HEAD"])


def _resolve_session_id(spec):
    """Return spec.session_id if set, otherwise generate one."""
    sid = getattr(spec, "session_id", "")
    if sid:
        return sid
    kind = getattr(spec, "read_only", False)
    prefix = "codeqa" if kind else "code"
    return _generate_session_id(prefix)


def write_session_card(spec, report, output_dir, kind="task_session",
                     environment_info: dict | None = None):
    """Write session.yaml into output_dir after a run completes."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    sid = _resolve_session_id(spec)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # Preserve original created_at if card already exists (resume)
    existing = read_session_card(output_dir)
    created_at = existing["created_at"] if existing else now

    card = {
        "schema_version": 1,
        "session_id": sid,
        "module": "codingagent",
        "kind": kind,
        "status": report.status,
        "created_at": created_at,
        "updated_at": now,
        "parent": spec.parent_run if getattr(spec, "parent_run", None) else None,
        "summary": report.summary[:300] if report.summary else "",
        "key_artifacts": [],
    }

    # Collect key artifacts
    if getattr(report, "diff_path", None):
        card["key_artifacts"].append({
            "type": "diff",
            "path": str(Path(report.diff_path).name) if report.diff_path else "",
            "summary": "Unified diff of all changes",
        })
    card["key_artifacts"].append({
        "type": "report",
        "path": "patch_report.md",
        "summary": report.summary[:200] if report.summary else "",
    })

    # Workspace path for resume
    if hasattr(spec, "workspace_path"):
        card["project_path"] = str(spec.workspace_path)

    # Session bindings (additive schema, see EXECUTION_CONTRACT_V1)
    if hasattr(spec, "workspace_path") and spec.workspace_path:
        origin, commit = _git_info(spec.workspace_path)
        repo_binding = {
            "path": str(spec.workspace_path),
            "origin": getattr(spec, "repo_url", "") or origin or "local",
            "commit": commit,
            "mode": "isolated" if getattr(spec, "repo_url", "") else "shared",
        }
        bindings: dict = {"repo": repo_binding}
        if getattr(spec, "env_name", ""):
            env_binding = {
                "name": spec.env_name,
                "policy": getattr(spec, "env_policy", "auto"),
                "fingerprint": "",
                "certification": "verification",
                "certified_at": "",
                "audit_artifact": "",
            }
            if environment_info:
                # content-addressed extras (additive, optional for readers)
                env_binding["env_id"] = environment_info.get("env_id", "")
                env_binding["manifest_path"] = environment_info.get("manifest_path", "")
                env_binding["spec_fingerprint"] = environment_info.get("spec_fingerprint", "")
                env_binding["resolved_fingerprint"] = environment_info.get("resolved_fingerprint", "")
                env_binding["prefix"] = environment_info.get("prefix", "")
                env_binding["certification"] = environment_info.get("certification", "verification")
            bindings["environment"] = env_binding
        card["bindings"] = bindings

    with open(output_dir / "session.yaml", "w") as f:
        yaml.dump(card, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    return sid


def read_session_card(output_dir):
    """Read session.yaml from output_dir, returning dict or None."""
    p = Path(output_dir) / "session.yaml"
    if not p.exists():
        return None
    with open(p) as f:
        return yaml.safe_load(f)


def list_sessions(root):
    """Scan root recursively for session.yaml files, returning list of dicts."""
    results = []
    for p in sorted(Path(root).rglob("session.yaml")):
        try:
            card = yaml.safe_load(p.read_text())
            card["_manifest_path"] = str(p)
            results.append(card)
        except Exception:
            continue
    return results


def session_status(output_dir):
    """Return a summary dict for a session: card data + state.json snippet."""
    card = read_session_card(output_dir)
    if not card:
        return {"error": "no session.yaml found"}

    status = dict(card)
    state_path = Path(output_dir) / "state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text())
        status["steps_count"] = len(state.get("steps", []))
        status["report_summary"] = state.get("report", {}).get("summary", "")[:300]
    return status
