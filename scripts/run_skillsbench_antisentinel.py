"""Register the in-process AntiSentinel BenchFlow agent, then run bench CLI."""
from benchflow.agents.registry import register_agent
from benchflow.cli.main import app

register_agent(name="antisentinel", install_cmd="true", launch_cmd="true", protocol="session-factory", session_factory="antisentinel.evaluation.skillsbench_session:build_agent", requires_env=[], description="AntiSentinel RuntimeLoop for SkillsBench", skill_paths=["$WORKSPACE/skills"], api_protocol="openai-completions", supports_acp_set_model=False)

if __name__ == "__main__": app()
