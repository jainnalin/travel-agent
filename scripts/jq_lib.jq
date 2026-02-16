# ---------------------------------------------------------
# jq helper library for travel-agent run inspection
# ---------------------------------------------------------

# Return boolean value as string if present, otherwise "MISSING"
def get_bool_or_missing(obj; key):
  if (obj | has(key)) then
    (obj[key] | tostring)
  else
    "MISSING"
  end;

# Return value as string if present, otherwise "MISSING"
def get_or_missing(obj; key):
  if (obj | has(key)) then
    (obj[key] | tostring)
  else
    "MISSING"
  end;

# Extract a step by tool name from plan.ready
def step_by_tool(steps; tool):
  steps[] | select(.tool == tool);

# Convenience: extract args for a given tool
def step_args(steps; tool):
  (step_by_tool(steps; tool) | (.args // {}));

# Pretty printer for planner knobs
def planner_knobs_summary:
  (.scratch.planner_knobs // {})
  | to_entries
  | map("\(.key)=\(.value)")
  | join(", ");

# Pretty printer for executor policy
def executor_policy_summary(ep):
  "workers=\(ep.max_workers) "
  + "default_limit=\(ep.default_resource_limit) "
  + "amadeus=\(ep.resource_limits["provider:amadeus"] // 0)";

# Validate that all search steps are resource-tagged
def validate_search_resources(steps):
  [ steps[]
    | select(.tool=="hotel.search" or .tool=="flight.search")
    | (.resources // [])
  ] | all(. == ["provider:amadeus"]);

# Compute delta between the two step.started timestamps (hotel.search vs flight.search)
def parallel_delta(events):
  [ events[]
    | select(.kind=="step.started"
      and (.data.tool=="hotel.search" or .data.tool=="flight.search"))
    | {tool:.data.tool, ts:.ts}
  ] as $s
  | if ($s|length)==2 then
      ($s | sort_by(.tool) | .[1].ts - .[0].ts)
    else
      null
    end;
