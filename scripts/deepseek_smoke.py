from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from coding_agent import CodeTaskSpec, run_code_task


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="coding-agent-deepseek-")
    repo = Path(tmp) / "toy_repo"
    repo.mkdir()
    (repo / "train.py").write_text(
        "\n".join(
            [
                "def main():",
                "    accuracy = 0.5",
                "    print(f'accuracy {accuracy}')",
                "",
                "if __name__ == '__main__':",
                "    main()",
                "",
            ]
        ),
        encoding="utf-8",
    )
    run(repo, ["git", "init"])
    run(repo, ["git", "config", "user.email", "coding-agent@example.invalid"])
    run(repo, ["git", "config", "user.name", "CodingAgent"])
    run(repo, ["git", "add", "train.py"])
    run(repo, ["git", "commit", "-m", "init"])

    report = run_code_task(
        CodeTaskSpec(
            repo_path=repo,
            task_goal=(
                "Modify train.py minimally so the script reports a loss value "
                "in addition to accuracy, without changing the existing accuracy "
                "calculation or control flow."
            ),
            constraints=[
                "Only edit train.py.",
                "Keep the existing accuracy print as its own separate output line.",
                "Keep the patch minimal and easy to review.",
            ],
            verify_commands=["python train.py"],
            allowed_paths=["train.py"],
            max_iterations=1,
            max_steps=8,
            timeout_seconds=60,
            api_base="https://api.deepseek.com",
            api_key_env="DEEPSEEK_API_KEY",
            model="deepseek-v4-pro",
        )
    )

    print(f"repo={repo}")
    print(f"status={report.status}")
    print(f"changed_files={report.changed_files}")
    print(f"summary={report.summary}")
    for result in report.verification_results:
        print(f"verify={result.command} returncode={result.returncode}")
        print(result.stdout_path.read_text(encoding="utf-8").strip())
    print("diff:")
    print((repo / "coding_agent_run" / "diff.patch").read_text(encoding="utf-8"))
    state_text = (repo / "coding_agent_run" / "state.json").read_text(encoding="utf-8")
    print("state_path=" + str(repo / "coding_agent_run" / "state.json"))
    print("report_path=" + str(repo / "coding_agent_run" / "patch_report.md"))
    print("steps_recorded=" + str(state_text.count('"step":')))


def run(cwd: Path, command: list[str]) -> None:
    subprocess.run(command, cwd=cwd, check=True, capture_output=True, text=True)


if __name__ == "__main__":
    main()
