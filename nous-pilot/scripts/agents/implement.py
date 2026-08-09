from __future__ import annotations

import json
import os
from pathlib import Path

from pilot_payload import FILES
from phase_payload import FILES as PHASE_FILES

FILES = {**FILES, **PHASE_FILES}

context = json.loads(Path(os.environ["NOUS_AGENT_CONTEXT_FILE"]).read_text(encoding="utf-8"))
cycle = int(context.get("cycle", 1))
proposals = []
for index, (path, content) in enumerate(FILES.items(), 1):
    proposals.append({
        "action_id": f"pilot-cycle-{cycle}-write-{index:03d}",
        "kind": "filesystem.write",
        "targets": [path],
        "content": content,
        "expected_effect": f"write Nous Pilot Phase 1 artifact {path}",
    })
print(json.dumps({
    "summary": f"Implement: propose {len(proposals)} scoped source and test artifacts through the managed filesystem adapter.",
    "outputs": {
        "action_proposals": proposals,
        "changed_paths": list(FILES),
        "no_managed_effects": False,
    }
}, ensure_ascii=False))
