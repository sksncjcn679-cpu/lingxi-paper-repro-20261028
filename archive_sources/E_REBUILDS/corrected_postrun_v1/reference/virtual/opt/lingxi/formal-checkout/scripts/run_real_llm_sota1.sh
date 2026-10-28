#!/usr/bin/env bash
# OS-SOTA1.6: real-provider experiments (needs an API key). Runs through scripts/rt.py so the
# tested source is always this checkout (Windows PYTHONPATH ':' vs ';' pitfall of the 1.5 run).
#   export AGENT_LLM_PROVIDER=openai AGENT_LLM_API_KEY=... AGENT_LLM_BASE_URL=... AGENT_LLM_MODEL=...
#   MODE=bill   bash scripts/run_real_llm_sota1.sh 3   # G2: judge OFF, the bill arms only (cheap)
#   MODE=judge  bash scripts/run_real_llm_sota1.sh 3   # G3: judge ON, all 22 variants
#   MODE=all    bash scripts/run_real_llm_sota1.sh 3   # judge OFF all 22 variants
#   MODE=long   bash scripts/run_real_llm_sota1.sh 1   # G4: long session (ROUNDS, default 3)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PYBIN="${PYTHON:-python3}"; command -v "$PYBIN" >/dev/null 2>&1 || PYBIN=python
: "${AGENT_LLM_PROVIDER:?set AGENT_LLM_PROVIDER=openai}"
: "${AGENT_LLM_API_KEY:?set AGENT_LLM_API_KEY}"
REPEAT="${1:-3}"
MODE="${MODE:-bill}"
DAY="$(date +%Y%m%d)"
export AGENT_LLM_OVERHEAD_PROBE="${AGENT_LLM_OVERHEAD_PROBE:-1}"
RT=("$PYBIN" "$ROOT/scripts/rt.py")
case "$MODE" in
  bill)
    "${RT[@]}" external-llm --repeat "$REPEAT" --order balanced \
      --variants T_memory_pair,FULL,FULL_coalesce,FULL_group,T_memory_pair_coalesce,FULL_group_sv \
      --checkpoint "results/real_llm_bill_${DAY}.jsonl" ;;
  judge)
    "${RT[@]}" external-llm --repeat "$REPEAT" --order balanced --judge \
      --checkpoint "results/real_llm_judge_${DAY}.jsonl" ;;
  all)
    "${RT[@]}" external-llm --repeat "$REPEAT" --order balanced \
      --checkpoint "results/real_llm_all_${DAY}.jsonl" ;;
  long)
    "${RT[@]}" long-session --rounds "${ROUNDS:-3}" --repeat "$REPEAT" ;;
  *) echo "unknown MODE=$MODE (bill|judge|all|long)"; exit 2 ;;
esac
echo "done (rerun the same command to resume from the checkpoint)"
