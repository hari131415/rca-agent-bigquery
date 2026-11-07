"""A deterministic mock LLM: scripted replies, no network, no keys.

The mock keys on the [STEP ...] marker and the question in the
prompt, and returns canned JSON for each step. It is a stand-in,
not a claim about what a real model would say: the graph's
structure (prompt -> parse -> guardrail -> execute -> analyze ->
answer) is what is under test, and the eval in Phase 6 measures a
real model against this mock baseline. For a question it does not
recognise, it returns a generic exploratory query, never an error.
"""

from __future__ import annotations

import json
import re

from .base import LLMResponse

PLAN_BRAKE_2021 = {
    "steps": [
        {
            "id": "S1",
            "description": "Compare brake complaint rates by plant for model year 2021.",
            "hypotheses": ["H1", "H2", "H3"],
        }
    ],
    "hypotheses": [
        {
            "id": "H1",
            "text": "Brake pads from Acme Brakes at Riverside line 2 cause the 2021 brake spike.",
            "test": "Brake rate by plant for 2021; Riverside highest, supplier split shows Acme.",
        },
        {
            "id": "H2",
            "text": "A plant-wide Riverside issue, not supplier-specific.",
            "test": "Brake rate by plant for 2021; Riverside highest across both lines.",
        },
        {
            "id": "H3",
            "text": "A model-year-wide brake issue affecting all plants.",
            "test": "Brake rate by plant for 2021; all plants elevated together.",
        },
    ],
}

PLAN_STEERING_2019 = {
    "steps": [
        {
            "id": "S1",
            "description": "Compare steering complaint rates by plant for model year 2019.",
            "hypotheses": ["H1"],
        }
    ],
    "hypotheses": [
        {
            "id": "H1",
            "text": "Steering complaints are elevated at Lakeside for model year 2019.",
            "test": "Steering rate by plant for 2019; Lakeside highest.",
        }
    ],
}

PLAN_PILOT_VOLUME = {
    "steps": [
        {
            "id": "S1",
            "description": "Count synthetic complaints by model year for the Honda Pilot.",
            "hypotheses": ["H1", "H2"],
        },
        {
            "id": "S2",
            "description": "Compute complaint counts, production volume, and the rate "
            "per 1,000 by model year for the Honda Pilot.",
            "hypotheses": ["H1", "H2"],
        },
    ],
    "hypotheses": [
        {
            "id": "H1",
            "text": "More vehicles were built, so more complaints; the rate per 1,000 is flat.",
            "test": "The 2021 count rises with production volume while the rate stays flat.",
        },
        {
            "id": "H2",
            "text": "A real defect spike for the 2021 Pilot.",
            "test": "The rate per 1,000 rises in 2021.",
        },
    ],
}

PLAN_SUPPLIER = {
    "steps": [
        {
            "id": "S1",
            "description": "Compare brake complaint rates by plant for model year 2021.",
            "hypotheses": ["H1", "H2"],
        }
    ],
    "hypotheses": [
        {
            "id": "H1",
            "text": "Acme brake pads at Riverside cause the spike.",
            "test": "The supplier split at Riverside shows Acme.",
        },
        {
            "id": "H2",
            "text": "A plant-wide Riverside issue, not supplier-specific.",
            "test": "The supplier split at Riverside is even.",
        },
    ],
}

PLAN_AIRBAG_2020 = {
    "steps": [
        {
            "id": "S1",
            "description": "Compare airbag complaint rates by plant for model year 2020.",
            "hypotheses": ["H1", "H2"],
        }
    ],
    "hypotheses": [
        {
            "id": "H1",
            "text": "Airbag complaints rose at every plant equally; there is no plant story.",
            "test": "Airbag rate by plant for 2020; all plants within a few percent.",
        },
        {
            "id": "H2",
            "text": "One plant drives the airbag rise.",
            "test": "Airbag rate by plant for 2020; one plant far above the rest.",
        },
    ],
}

PLAN_RECALL = {
    "steps": [
        {
            "id": "S1",
            "description": "Find the recall campaign covering the make, model, and component.",
            "hypotheses": ["H1"],
        }
    ],
    "hypotheses": [
        {
            "id": "H1",
            "text": "A recall campaign covers the vehicles in question.",
            "test": "The recalls table has a campaign for the make, model, and component.",
        }
    ],
}

PLAN_RECALL_SHARE = {
    "steps": [
        {
            "id": "S1",
            "description": "Find the recall campaign covering the 2021 F-150 brake complaints.",
            "hypotheses": ["H1"],
        },
        {
            "id": "S2",
            "description": "Count real F-150 brake complaints by model year, to split "
            "covered from uncovered.",
            "hypotheses": ["H1"],
        },
    ],
    "hypotheses": [
        {
            "id": "H1",
            "text": "Recall 26V104000 covers the 2021 F-150 brake complaints, "
            "not the 2019-2020 ones.",
            "test": "The recall covers 2021-2026; the complaint counts by year "
            "show which years are covered.",
        }
    ],
}

PLAN_GENERIC = {
    "steps": [
        {
            "id": "S1",
            "description": "Compare complaint counts by make, model, and model year.",
            "hypotheses": ["H1"],
        }
    ],
    "hypotheses": [
        {
            "id": "H1",
            "text": "No specific cause is identifiable from counts alone.",
            "test": "Counts by make/model/year; look for an outlier.",
        }
    ],
}

RATE_BY_PLANT_SQL = """WITH built AS (
  SELECT plant_id, model_year, SUM(vehicles_built) AS vehicles
  FROM rca_agent.builds GROUP BY plant_id, model_year
), complaints AS (
  SELECT plant_id, model_year, COUNT(*) AS n,
    MIN(CASE WHEN is_synthetic THEN 1 ELSE 0 END) AS all_synthetic,
    MAX(CASE WHEN is_synthetic THEN 1 ELSE 0 END) AS any_synthetic
  FROM rca_agent.complaints
  WHERE is_synthetic AND compdesc = '{component}' AND model_year = {year}
  GROUP BY plant_id, model_year
)
SELECT c.plant_id, c.n, b.vehicles, c.n * 1000.0 / b.vehicles AS rate_per_1000,
  c.all_synthetic, c.any_synthetic
FROM complaints c JOIN built b
  ON b.plant_id = c.plant_id AND b.model_year = c.model_year
ORDER BY rate_per_1000 DESC"""

BAD_SQL = "SELECT * FROM other_dataset.secret"

RECALL_LOOKUP_SQL = """SELECT campno, make, model, model_year, potaff, compname
FROM rca_agent.recalls
WHERE campno = '{campaign}'"""

RECALL_SHARE_SQL = """SELECT model_year, COUNT(*) AS n
FROM rca_agent.complaints
WHERE make = 'FORD' AND model = 'F-150' AND compdesc = 'SERVICE BRAKES'
  AND NOT is_synthetic
GROUP BY model_year
ORDER BY model_year"""

RECENT_COMPLAINTS_SQL = """SELECT cmplid, make, model, model_year, compdesc, cdescr
FROM rca_agent.complaints
WHERE compdesc = 'SERVICE BRAKES' AND model_year = 2021
ORDER BY ldate DESC"""

GENERIC_SQL = """SELECT make, model, model_year, COUNT(*) AS n
FROM rca_agent.complaints
GROUP BY make, model, model_year
ORDER BY n DESC"""

PILOT_COUNT_SQL = """SELECT model_year, COUNT(*) AS n,
  MIN(CASE WHEN is_synthetic THEN 1 ELSE 0 END) AS all_synthetic,
  MAX(CASE WHEN is_synthetic THEN 1 ELSE 0 END) AS any_synthetic
FROM rca_agent.complaints
WHERE make = 'HONDA' AND model = 'PILOT' AND is_synthetic
GROUP BY model_year
ORDER BY model_year"""

PILOT_RATE_SQL = """WITH built AS (
  SELECT make, model, model_year, SUM(vehicles_built) AS vehicles
  FROM rca_agent.builds GROUP BY make, model, model_year
), complaints AS (
  SELECT make, model, model_year, COUNT(*) AS n,
    MIN(CASE WHEN is_synthetic THEN 1 ELSE 0 END) AS all_synthetic,
    MAX(CASE WHEN is_synthetic THEN 1 ELSE 0 END) AS any_synthetic
  FROM rca_agent.complaints
  WHERE make = 'HONDA' AND model = 'PILOT' AND is_synthetic
  GROUP BY make, model, model_year
)
SELECT c.model_year, c.n, b.vehicles, c.n * 1000.0 / b.vehicles AS rate_per_1000,
  c.all_synthetic, c.any_synthetic
FROM complaints c JOIN built b
  ON b.make = c.make AND b.model = c.model AND b.model_year = c.model_year
ORDER BY c.model_year"""

SUPPLIER_SPLIT_SQL = """WITH built AS (
  SELECT supplier_brake, SUM(vehicles_built) AS vehicles
  FROM rca_agent.builds
  WHERE plant_id = 'riverside' AND model_year = 2021
  GROUP BY supplier_brake
), complaints AS (
  SELECT supplier_id, COUNT(*) AS n,
    MIN(CASE WHEN is_synthetic THEN 1 ELSE 0 END) AS all_synthetic,
    MAX(CASE WHEN is_synthetic THEN 1 ELSE 0 END) AS any_synthetic
  FROM rca_agent.complaints
  WHERE is_synthetic AND compdesc = 'SERVICE BRAKES' AND model_year = 2021
    AND plant_id = 'riverside'
  GROUP BY supplier_id
)
SELECT c.supplier_id, c.n, b.vehicles, c.n * 1000.0 / b.vehicles AS rate_per_1000,
  c.all_synthetic, c.any_synthetic
FROM complaints c JOIN built b ON b.supplier_brake = c.supplier_id
ORDER BY rate_per_1000 DESC"""

ANALYZE_BRAKE_2021 = {
    "summary": "Brake complaints for model year 2021 concentrate at Riverside.",
    "verdict": {"cause_type": "supplier", "entity": "acme-brakes", "supporting_queries": [0]},
    "ranked_hypotheses": [
        {
            "id": "H1",
            "rank": 1,
            "verdict": "supported",
            "evidence": "Riverside brake rate 4.14 per 1,000, against 1.22 at the next plant.",
        },
        {
            "id": "H2",
            "rank": 2,
            "verdict": "not supported",
            "evidence": "The rate differs by plant, so a plant-wide story does not fit alone.",
        },
        {
            "id": "H3",
            "rank": 3,
            "verdict": "not supported",
            "evidence": "Other plants are flat, so this is not model-year-wide.",
        },
    ],
}

ANALYZE_STEERING_2019 = {
    "summary": "Steering complaints for model year 2019 concentrate at Lakeside.",
    "verdict": {"cause_type": "plant", "entity": "lakeside", "supporting_queries": [0]},
    "ranked_hypotheses": [
        {
            "id": "H1",
            "rank": 1,
            "verdict": "supported",
            "evidence": "Lakeside steering rate 3.21 per 1,000, against 0.81 at the next plant.",
        }
    ],
}

ANALYZE_NONE = {
    "summary": "No plant stands out; rates are flat across plants.",
    "verdict": {"cause_type": "none", "entity": None, "supporting_queries": [0]},
    "ranked_hypotheses": [
        {
            "id": "H1",
            "rank": 1,
            "verdict": "not supported",
            "evidence": "Plant rates are close to each other, with no outlier.",
        }
    ],
}

ANALYZE_TEXT = {
    "summary": "Recent brake complaints were reviewed; the text is "
    "owner-reported and unverified, and no single cause is identifiable "
    "from the text alone.",
    "verdict": {"cause_type": "none", "entity": None, "supporting_queries": [0]},
    "ranked_hypotheses": [
        {
            "id": "H1",
            "rank": 1,
            "verdict": "not supported",
            "evidence": "Complaint text alone does not establish a cause.",
        }
    ],
}

ANALYZE_PILOT = {
    "summary": "Total complaints rose because production doubled; the rate "
    "per 1,000 vehicles built is flat.",
    "verdict": {"cause_type": "volume", "entity": "honda-pilot", "supporting_queries": [0, 1]},
    "ranked_hypotheses": [
        {
            "id": "H1",
            "rank": 1,
            "verdict": "supported",
            "evidence": "The 2021 count (2,284) is roughly double the 2020 count (1,056), "
            "matching production volume, which also roughly doubled (139,960 to 287,855). "
            "The rate per 1,000 is 7.55 in 2020 and 7.93 in 2021.",
        },
        {
            "id": "H2",
            "rank": 2,
            "verdict": "not supported",
            "evidence": "The rate did not rise; it is 7.55 in 2020 and 7.93 in 2021 per 1,000.",
        },
    ],
}

ANALYZE_SUPPLIER_REQUEST = {
    "summary": "Riverside is highest, but the plant-level evidence does not "
    "distinguish a supplier effect from a plant effect.",
    "ranked_hypotheses": [],
    "follow_up": {
        "description": "Break down Riverside brake complaints by supplier for 2021.",
        "reason": "The plant-level evidence does not distinguish a supplier effect "
        "from a plant effect.",
    },
}

ANALYZE_AIRBAG_2020 = {
    "summary": "Airbag complaints for model year 2020 rose at every plant equally; "
    "no plant stands out.",
    "verdict": {"cause_type": "fleet_wide", "entity": None, "supporting_queries": [0]},
    "ranked_hypotheses": [
        {
            "id": "H1",
            "rank": 1,
            "verdict": "supported",
            "evidence": "Airbag rates are 1.80 to 1.83 per 1,000 at every plant "
            "(Riverside 1.83, Summit 1.82, Lakeside 1.81, Harborview 1.80).",
        },
        {
            "id": "H2",
            "rank": 2,
            "verdict": "not supported",
            "evidence": "No plant is far above the rest; the highest rate is 1.83 "
            "and the lowest 1.80.",
        },
    ],
}

# Recall analyses are keyed by campaign. The numbers are the real
# recall records: potaff is the affected-units column, and the
# complaint counts are real rows only (is_synthetic = FALSE).
ANALYZE_RECALL_26V104000 = {
    "summary": "Recall 26V104000 covers the 2021 F-150: the integrated "
    "trailer brake control module may lose communication when towing.",
    "verdict": {"cause_type": "recall", "entity": "26V104000", "supporting_queries": [0]},
    "ranked_hypotheses": [
        {
            "id": "H1",
            "rank": 1,
            "verdict": "supported",
            "evidence": "Recall 26V104000 covers the 2021-2026 F-150, 4,381,878 units affected.",
        }
    ],
}

ANALYZE_RECALL_23V858000 = {
    "summary": "Recall 23V858000 covers the Honda Pilot fuel pump.",
    "verdict": {"cause_type": "recall", "entity": "23V858000", "supporting_queries": [0]},
    "ranked_hypotheses": [
        {
            "id": "H1",
            "rank": 1,
            "verdict": "supported",
            "evidence": "Recall 23V858000 covers the 2019-2021 Pilot, "
            "2,490,460 units affected, for a fuel pump defect.",
        }
    ],
}

ANALYZE_RECALL_20V439000 = {
    "summary": "Recall 20V439000 covers the Honda Pilot rearview camera.",
    "verdict": {"cause_type": "recall", "entity": "20V439000", "supporting_queries": [0]},
    "ranked_hypotheses": [
        {
            "id": "H1",
            "rank": 1,
            "verdict": "supported",
            "evidence": "Recall 20V439000 covers the 2019-2021 Pilot, "
            "607,786 units affected, for a rearview camera defect.",
        }
    ],
}

ANALYZE_RECALL_20V440000 = {
    "summary": "Recall 20V440000 covers the Honda Pilot rearview camera.",
    "verdict": {"cause_type": "recall", "entity": "20V440000", "supporting_queries": [0]},
    "ranked_hypotheses": [
        {
            "id": "H1",
            "rank": 1,
            "verdict": "supported",
            "evidence": "Recall 20V440000 covers the 2019-2021 Pilot, "
            "499,344 units affected, for a rearview camera defect.",
        }
    ],
}

ANALYZE_RECALL_SHARE = {
    "summary": "Recall 26V104000 covers the 2021 F-150 brake complaints; "
    "the 2019-2020 complaints are not covered.",
    "verdict": {"cause_type": "recall", "entity": "26V104000", "supporting_queries": [0, 1]},
    "ranked_hypotheses": [
        {
            "id": "H1",
            "rank": 1,
            "verdict": "supported",
            "evidence": "Recall 26V104000 covers the 2021-2026 F-150, "
            "4,381,878 units affected. Of 89 real F-150 brake complaints, "
            "53 are for model year 2021 and covered; the rest are for "
            "2019-2020 and are not covered.",
        }
    ],
}

ANALYZE_SUPPLIER_DONE = {
    "summary": "The brake spike at Riverside is a supplier effect: Acme pads.",
    "verdict": {"cause_type": "supplier", "entity": "acme-brakes", "supporting_queries": [0, 1]},
    "ranked_hypotheses": [
        {
            "id": "H1",
            "rank": 1,
            "verdict": "supported",
            "evidence": "Acme brake pads show 7.20 complaints per 1,000 vehicles built "
            "(956 complaints over 132,797 vehicles), against 1.17 for Beacon "
            "(160 over 136,493).",
        },
        {
            "id": "H2",
            "rank": 2,
            "verdict": "not supported",
            "evidence": "The rate is not even: 7.20 for Acme against 1.17 for Beacon.",
        },
    ],
}

_STEP_RE = re.compile(r"\[STEP (\w+)\]")
_QUESTION_RE = re.compile(r"^Question: (.*)$", re.MULTILINE)
_PLAN_STEP_RE = re.compile(r"^Plan step: (S\d+)", re.MULTILINE)


_RECALL_CAMPAIGNS = ("26v104000", "23v858000", "20v439000", "20v440000")


def _recall_campaign(question: str) -> str | None:
    q = question.lower()
    for campaign in _RECALL_CAMPAIGNS:
        if campaign in q:
            return campaign.upper()
    if "trailer" in q and "recall" in q:
        return "26V104000"
    if "fuel pump" in q and "recall" in q:
        return "23V858000"
    if "f-150" in q and "brake" in q and "recall" in q:
        return "26V104000"
    if "pilot" in q and "recall" in q:
        return "23V858000"
    return None


def _kind(question: str) -> str:
    q = question.lower()
    if "recall" in q:
        if "share" in q:
            return "recall_share"
        return "recall"
    if "airbag" in q and "2020" in q:
        return "airbag"
    if "supplier" in q and "brake" in q:
        return "supplier"
    if "pilot" in q and ("rise" in q or "volume" in q or "total" in q or "built" in q):
        return "pilot"
    if "brake" in q and "2021" in q and ("show" in q or "recent" in q or "list" in q):
        return "text"
    if "brake" in q and "2021" in q:
        return "brake2021"
    if "steering" in q and "2019" in q:
        return "steering2019"
    if "engine" in q:
        return "none"
    return "generic"


class MockLLM:
    """Scripted completions. The step and question come from the prompt;
    nothing else in the prompt (including evidence text) changes the
    reply, which is the property the injection test relies on."""

    def complete(self, prompt: str) -> LLMResponse:
        step = _STEP_RE.search(prompt)
        step_name = step.group(1) if step else "unknown"
        question_match = _QUESTION_RE.search(prompt)
        question = question_match.group(1) if question_match else ""
        kind = _kind(question)
        plan_step = _PLAN_STEP_RE.search(prompt)
        step_id = plan_step.group(1) if plan_step else "S1"
        is_follow_up = "Follow-up:" in prompt
        has_supplier_evidence = "supplier_id" in prompt
        text = self._reply(step_name, kind, step_id, is_follow_up, has_supplier_evidence, question)
        return LLMResponse(
            text=text,
            tokens_in=max(1, len(prompt) // 4),
            tokens_out=max(1, len(text) // 4),
            cost_usd=0.0,
        )

    def _reply(
        self,
        step: str,
        kind: str,
        step_id: str,
        is_follow_up: bool,
        has_supplier_evidence: bool,
        question: str = "",
    ) -> str:
        if step == "plan":
            plan = {
                "brake2021": PLAN_BRAKE_2021,
                "steering2019": PLAN_STEERING_2019,
                "pilot": PLAN_PILOT_VOLUME,
                "supplier": PLAN_SUPPLIER,
                "airbag": PLAN_AIRBAG_2020,
                "recall": PLAN_RECALL,
                "recall_share": PLAN_RECALL_SHARE,
                "text": PLAN_GENERIC,
                "none": PLAN_GENERIC,
                "generic": PLAN_GENERIC,
            }[kind]
            return json.dumps(plan)
        if step == "sql":
            if is_follow_up:
                return json.dumps({"sql": SUPPLIER_SPLIT_SQL})
            if kind in ("recall", "recall_share"):
                campaign = _recall_campaign(question) or "26V104000"
                if kind == "recall_share" and step_id == "S2":
                    return json.dumps({"sql": RECALL_SHARE_SQL})
                return json.dumps({"sql": RECALL_LOOKUP_SQL.format(campaign=campaign)})
            if kind == "airbag":
                return json.dumps(
                    {"sql": RATE_BY_PLANT_SQL.format(component="AIR BAGS", year=2020)}
                )
            if kind == "brake2021":
                return json.dumps(
                    {"sql": RATE_BY_PLANT_SQL.format(component="SERVICE BRAKES", year=2021)}
                )
            if kind == "supplier":
                return json.dumps(
                    {"sql": RATE_BY_PLANT_SQL.format(component="SERVICE BRAKES", year=2021)}
                )
            if kind == "steering2019":
                return json.dumps({"sql": BAD_SQL})
            if kind == "pilot":
                return json.dumps({"sql": PILOT_COUNT_SQL if step_id == "S1" else PILOT_RATE_SQL})
            if kind == "text":
                return json.dumps({"sql": RECENT_COMPLAINTS_SQL})
            if kind == "none":
                return json.dumps({"sql": RATE_BY_PLANT_SQL.format(component="ENGINE", year=2019)})
            return json.dumps({"sql": GENERIC_SQL})
        if step == "correct":
            if kind in ("recall", "recall_share"):
                campaign = _recall_campaign(question) or "26V104000"
                if kind == "recall_share" and step_id == "S2":
                    return json.dumps({"sql": RECALL_SHARE_SQL})
                return json.dumps({"sql": RECALL_LOOKUP_SQL.format(campaign=campaign)})
            if kind == "airbag":
                return json.dumps(
                    {"sql": RATE_BY_PLANT_SQL.format(component="AIR BAGS", year=2020)}
                )
            if kind == "steering2019":
                return json.dumps(
                    {"sql": RATE_BY_PLANT_SQL.format(component="STEERING", year=2019)}
                )
            if kind == "brake2021":
                return json.dumps(
                    {"sql": RATE_BY_PLANT_SQL.format(component="SERVICE BRAKES", year=2021)}
                )
            if kind == "supplier":
                return json.dumps(
                    {"sql": RATE_BY_PLANT_SQL.format(component="SERVICE BRAKES", year=2021)}
                )
            if kind == "pilot":
                return json.dumps({"sql": PILOT_COUNT_SQL if step_id == "S1" else PILOT_RATE_SQL})
            if kind == "text":
                return json.dumps({"sql": RECENT_COMPLAINTS_SQL})
            if kind == "none":
                return json.dumps({"sql": RATE_BY_PLANT_SQL.format(component="ENGINE", year=2019)})
            return json.dumps({"sql": GENERIC_SQL})
        if step == "analyze":
            if kind == "supplier" and not has_supplier_evidence:
                return json.dumps(ANALYZE_SUPPLIER_REQUEST)
            if kind == "supplier":
                return json.dumps(ANALYZE_SUPPLIER_DONE)
            if kind == "recall_share":
                return json.dumps(ANALYZE_RECALL_SHARE)
            if kind == "recall":
                campaign = _recall_campaign(question) or "26V104000"
                return json.dumps(
                    {
                        "26V104000": ANALYZE_RECALL_26V104000,
                        "23V858000": ANALYZE_RECALL_23V858000,
                        "20V439000": ANALYZE_RECALL_20V439000,
                        "20V440000": ANALYZE_RECALL_20V440000,
                    }[campaign]
                )
            analysis = {
                "brake2021": ANALYZE_BRAKE_2021,
                "steering2019": ANALYZE_STEERING_2019,
                "pilot": ANALYZE_PILOT,
                "airbag": ANALYZE_AIRBAG_2020,
                "text": ANALYZE_TEXT,
                "none": ANALYZE_NONE,
                "generic": ANALYZE_NONE,
            }[kind]
            return json.dumps(analysis)
        return json.dumps({"error": f"unknown step: {step}"})
