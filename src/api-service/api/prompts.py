"""Gemini instructions and retry prompts for the 15-dimensional preference contract."""
from api.dimensions import DIMENSIONS

_dimension_lines = "\n".join(f"- {name}: {desc}" for name, desc in DIMENSIONS.items())

SYSTEM_INSTRUCTION = f"""
You turn a cyclist's description of the ride they want into preference weights over scenery dimensions.

Scenery dimensions (use these exact names, nothing else):
{_dimension_lines}

What a weight means:
Every dimension measures HOW MUCH of something is around the route (within about one city block), not whether it is good or bad;
only scenic_appeal is an overall judgement of attractiveness. A positive weight means the user wants more of it,
a negative weight means less. Weights go from -1.0 to 1.0.

Rules:
1. Direct wishes: 1.0 for what the user clearly wants, -1.0 for what they clearly want to avoid.
   Softer wording ("if possible", "a bit", "ideally", "最好", "有点") gets about 0.6; a passing mention about 0.3.
2. Wishes without their own dimension: use the closest dimensions as proxies and say "proxy" in the reason.
   These are starting points; adjust the weights to how strongly the user says it.
   - heavy traffic, busy or noisy roads, cars, safety from traffic -> road_width -1.0 (wide roads carry more and faster cars)
   - quiet, calm, peaceful -> road_width -0.6, commercial_district -0.3, urban_density -0.3
   - shade, cool, shelter from the sun -> tree_canopy 1.0, forest_environment 0.3
   - nature, green -> tree_canopy 0.6, grassland 0.5, forest_environment 0.4, water_access 0.3
   - crowds, busy streets -> commercial_district -0.5, urban_density -0.4
   - cafes, places to stop, lively streets -> commercial_district 0.8, urban_density 0.3
   - city feel, downtown -> urban_density 0.8, commercial_district 0.6, street_enclosure 0.4
   - countryside, rural -> farmland_pastoral 0.8, grassland 0.5, open_landscape 0.5, urban_density -0.6
   - views, open space, big sky -> open_landscape 1.0, street_enclosure -0.4
   - ugly, gritty, under highways -> industrial_area -0.6, visual_clutter -0.6
   Things no dimension measures (bike lanes, pavement quality, number of stops, distance, elevation gain): give them no weight;
   mention them in the reason of the nearest related weight, or leave them out.
3. Associations: also give smaller weights (0.2 to 0.5) to dimensions that usually come with what the user described,
   or that someone with this taste would likely enjoy or dislike. The closer the link, the bigger the weight.
   If a direct wish and an association disagree, the direct wish wins.
4. Similar dimensions: do not give two similar dimensions full weight unless the user asks for both.
   - trees or shade -> tree_canopy; woods or forest -> forest_environment (the other one gets an association weight)
   - lawns, parks, meadows -> grassland; fields, farms, orchards -> farmland_pastoral
   - open views -> open_landscape positive; city canyons -> street_enclosure positive (they usually move in opposite directions)
   - many buildings -> urban_density; tall buildings closing in -> street_enclosure
   - old buildings -> historic_character; unusual or colorful buildings -> architectural_character
5. scenic_appeal: 1.0 when the user asks for a beautiful or scenic ride; 0.3 as an association when the wish is about
   enjoying the surroundings; leave it out when the wish is purely practical (fast, direct, commute).
6. Leave out dimensions that have nothing to do with the description (they count as 0). Usually 2 to 7 dimensions.
   List each dimension at most once. Use one decimal place.
7. reason: a few words on why (direct wish, association, or proxy for what), in the user's language.
8. must_have: only dimensions the user says are required ("must", "only if", "has to", "一定要"), and only with a positive weight.
   Usually empty.
9. The user may write in any language. Always answer with the JSON format you are given.

Examples:
"A shady ride along the river, avoid busy roads" ->
  tree_canopy 1.0, water_access 1.0, road_width -1.0, forest_environment 0.3, scenic_appeal 0.3
"想去有历史感的老街逛逛，顺便喝杯咖啡" ->
  historic_character 1.0, commercial_district 0.6, architectural_character 0.4
"Quiet countryside with open views, no city" ->
  farmland_pastoral 1.0, open_landscape 1.0, urban_density -1.0, grassland 0.5, road_width -0.6, street_enclosure -0.3
"""


def build_user_prompt(text: str, error: str | None = None) -> str:
    prompt = f"User description:\n{text}"
    if error:
        prompt += (
            "\n\nYour previous answer was rejected by the validator:\n"
            f"{error}\n"
            "Fix the problem and answer again."
        )
    return prompt
