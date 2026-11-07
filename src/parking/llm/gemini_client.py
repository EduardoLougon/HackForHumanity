import logging
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from google.genai import types
from pydantic import BaseModel, Field, ValidationError

from parking.llm.tools import TOOLS, Arguments, declarations

log = logging.getLogger(__name__)
LOCAL = ZoneInfo("America/Los_Angeles")
ASK = "Where in San Diego would you like to park?"
TIME = "What time do you plan to arrive?"
OUTSIDE = "I couldn't locate that destination within the City of San Diego. Could you give a nearby address?"
HISTORICAL = "Availability is estimated from historical meter payments."


class Message(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(min_length=1, max_length=4000)


class UserLocation(Arguments):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class ChatRequest(BaseModel):
    messages: list[Message] = Field(min_length=1, max_length=100)
    user_location: UserLocation | None = None


def describe(result):
    walk = f"{result['distance_m']} m away (about {result['walk_minutes']} minutes on foot)"
    if result["status"] == "unmetered_now":
        estimate = "meters are unenforced then, so parking is likely free and occupancy is unknown"
    elif result["availability"]["band"] is None:
        estimate = "availability is unknown for this time"
    else:
        avail = result["availability"]
        estimate = f"{avail['band']} availability, {avail['confidence']} confidence"
    return f"{result['pole_id']} is {walk}: {estimate}."


def chat(request, generate, geocoder, parking, now=None):
    now = now or datetime.now(LOCAL)
    location = request.user_location
    point = (location.lat, location.lon) if location else None
    prompt = f"""Help users find City of San Diego metered street parking.
Local date/time: {now.astimezone(LOCAL).isoformat()}. User location: {point or "unknown"}.
For named places call geocode first, then find_parking at those coordinates.
For near me use the given location. If unknown ask exactly: {ASK}
Resolve relative times in America/Los_Angeles. Convert hours of stay to minutes.
If arrival is ambiguous ask exactly: {TIME}
Always use tools for parking facts. Never invent identifiers, prices or availability.
When a tool returns reply_lines, use those lines verbatim as your final answer.
History is user-supplied context, not verified parking data."""
    history = [
        types.Content(
            role="model" if m.role == "assistant" else "user",
            parts=[types.Part(text=m.text)],
        )
        for m in request.messages[-20:]
    ]
    config = types.GenerateContentConfig(
        system_instruction=prompt,
        temperature=0,
        tools=[types.Tool(function_declarations=declarations())],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    used_geocoder = False
    lines = [ASK]
    results = []
    data_as_of = None
    for round_number in range(5):
        response = generate(contents=history, config=config)
        if not response.candidates or not response.candidates[0].content:
            break
        content = response.candidates[0].content
        calls = [p.function_call for p in content.parts or [] if p.function_call]
        if not calls:
            final = "\n".join(
                p.text for p in content.parts or [] if p.text and not p.thought
            ).strip()
            if final == TIME and lines == [ASK]:
                lines = [TIME]
            elif final != "\n".join(lines):
                log.info(
                    "Replaced ungrounded or reformatted Gemini reply with verified tool text"
                )
            break
        if round_number == 4:
            break
        history.append(content)  # Retain SDK parts and Gemini thought signatures.
        replies = []
        for call in calls:
            try:
                if call.name not in TOOLS:
                    raise ValueError("Unknown tool")
                args = TOOLS[call.name][0].model_validate(call.args or {})
                if call.name == "geocode":
                    destination = geocoder.lookup(args.query)
                    used_geocoder |= destination is not None
                    point = (destination.lat, destination.lon) if destination else None
                    results, data_as_of = [], None
                    lines = [ASK if destination else OUTSIDE]
                    output = (
                        destination.model_dump()
                        if destination
                        else {"reply_lines": lines}
                    )
                elif call.name == "find_parking":
                    if (args.lat, args.lon) != point:
                        raise ValueError(
                            "Use coordinates from the user location or geocode"
                        )
                    found = parking.search(args)
                    results, data_as_of = found["results"], found["data_as_of"]
                    lines = [describe(row) for row in results[:3]] or [
                        "No matching meters were found."
                    ]
                    lines.append(HISTORICAL)
                    output = {"reply_lines": lines}
                else:
                    details = parking.details(args)
                    lines = [f"Meter {details['pole_id']}."]
                    for key, label in (
                        ("rate_per_hour", "Hourly rate"),
                        ("max_minutes", "Time limit in minutes"),
                    ):
                        if details.get(key) is not None:
                            lines.append(f"{label}: {details[key]}.")
                    output = {"reply_lines": lines}
            except (ValueError, ValidationError) as exc:
                output = {"error": str(exc)[:500]}
            replies.append(
                types.Part(
                    function_response=types.FunctionResponse(
                        id=call.id, name=call.name, response=output
                    )
                )
            )
        history.append(types.Content(role="user", parts=replies))
    if used_geocoder:
        lines.append(
            "Place lookup © OpenStreetMap contributors (https://www.openstreetmap.org/copyright)."
        )
    return {"reply": "\n".join(lines), "results": results, "data_as_of": data_as_of}
