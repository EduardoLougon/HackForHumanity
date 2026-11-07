# Frontend Integration Guide — San Diego Parking Finder API

This guide provides everything the frontend team needs to connect a web or mobile map interface and chat box to the backend API.

---

## 1. System Overview: How It Works Under the Hood

### The Core Problem & Solution
- **The City of San Diego does not have real-time space sensors on street meters.** The city publishes historical payment transaction logs (updated daily).
- Therefore, this system **cannot guarantee that a specific spot is empty right now**.
- Instead, the backend **predicts the historical likelihood of finding an open spot** at a user's chosen arrival time (either "now" or an arbitrary future time like tomorrow at 7 PM).
- It groups payments into 15-minute time buckets over the past 6 months, applies recency weighting (recent months count more), accounts for California holidays, and scores spots based on:
  $$\text{Score} = P(\text{available}) \times e^{-\frac{\text{Distance in meters}}{250}}$$

### Design Rule: Deterministic Results vs. LLM Text
- **The frontend map pins MUST be rendered from the structured `results` array, NEVER parsed out of Gemini's text response.**
- Gemini's job is solely natural language understanding (converting phrases like *"near Petco Park tomorrow at 7pm for 2 hours"* into API parameters) and generating friendly conversational explanations.
- Map coordinates, probabilities, walking times, and prices always come from the backend's deterministic math.

---

## 2. Base URL & Environments

- **Local Development**: `http://localhost:8000`
- **Render Production**: `https://<your-render-service-name>.onrender.com`
- **Interactive Swagger Docs**: `http://localhost:8000/docs`

---

## 3. Endpoints Reference

### 3.1 Deterministic Spot Search: `GET /v1/spots`
Use this endpoint when the user interacts with the map (e.g. clicking a location, moving the map center, or submitting a structured search form).

#### Query Parameters:
| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `lat` | `float` | **Yes** | — | Target latitude (e.g. `32.7073` for Petco Park). Must be within San Diego bounds (32.5 to 33.3). |
| `lon` | `float` | **Yes** | — | Target longitude (e.g. `-117.1566`). Must be within San Diego bounds (-117.6 to -116.8). |
| `arrival_time` | `string` | No | Current local time | ISO 8601 string (e.g. `2026-11-09T14:00:00-08:00`). Supports "now" or future dates. |
| `duration_minutes`| `int` | No | `null` | Planned parking stay in minutes (e.g. `120` for 2 hours). |
| `radius_m` | `int` | No | `400` | Search radius in meters (min `50`, max `2000`). |
| `limit` | `int` | No | `10` | Maximum number of ranked spots to return (min `1`, max `50`). |

#### Example Request:
```bash
curl "http://localhost:8000/v1/spots?lat=32.7073&lon=-117.1566&arrival_time=2026-11-09T14:00:00-08:00&duration_minutes=120&radius_m=500&limit=5"
```

#### Example Response:
```json
{
  "query": {
    "lat": 32.7073,
    "lon": -117.1566,
    "arrival_time": "2026-11-09T14:00:00-08:00",
    "duration_minutes": 120,
    "radius_m": 500,
    "max_results": 5
  },
  "data_as_of": "2026-11-06T21:54:00",
  "results": [
    {
      "pole_id": "7-724",
      "lat": 32.7081,
      "lon": -117.1559,
      "distance_m": 110.2,
      "walk_minutes": 1.4,
      "status": "metered",
      "availability": {
        "p_available": 0.64,
        "band": "likely",
        "confidence": "high",
        "basis": "pole"
      },
      "meter_type": "single_space",
      "rate_per_hour": 2.50,
      "max_minutes": 120,
      "limit_exceeded": false,
      "area": "East Village",
      "sub_area": "700 SEVENTH AVE"
    },
    {
      "pole_id": "8-715",
      "lat": 32.7085,
      "lon": -117.1550,
      "distance_m": 220.0,
      "walk_minutes": 2.8,
      "status": "metered",
      "availability": {
        "p_available": 0.42,
        "band": "possible",
        "confidence": "medium",
        "basis": "pole"
      },
      "meter_type": "single_space",
      "rate_per_hour": 2.50,
      "max_minutes": 60,
      "limit_exceeded": true,
      "note": "Stay duration (120m) exceeds meter limit (60m).",
      "area": "East Village",
      "sub_area": "700 EIGHTH AVE"
    }
  ],
  "notes": [
    "Estimates come from historical meter payments, not live sensors.",
    "Walking times are straight-line approximations."
  ]
}
```

---

### 3.2 Conversational Assistant: `POST /v1/chat`
Use this endpoint for the chat box. The frontend sends conversation history and optional GPS coordinates.

#### Request Body:
```json
{
  "messages": [
    { "role": "user", "text": "Find parking near Petco Park tomorrow at 7pm for 2 hours" }
  ],
  "user_location": {
    "lat": 32.7157,
    "lon": -117.1611
  }
}
```

#### Response Body:
```json
{
  "reply": "I found 5 street parking options near Petco Park for tomorrow at 7:00 PM. The closest space is at pole 7-724 on 700 SEVENTH AVE (110m away, ~1.4 min walk) and is likely open at arrival. Rate is $2.50/hr. (Note: Estimates come from historical payment patterns, not live sensors).",
  "results": [
    {
      "pole_id": "7-724",
      "lat": 32.7081,
      "lon": -117.1559,
      "distance_m": 110.2,
      "walk_minutes": 1.4,
      "status": "metered",
      "availability": {
        "p_available": 0.64,
        "band": "likely",
        "confidence": "high",
        "basis": "pole"
      },
      "meter_type": "single_space",
      "rate_per_hour": 2.50,
      "max_minutes": 120,
      "limit_exceeded": false,
      "area": "East Village",
      "sub_area": "700 SEVENTH AVE"
    }
  ],
  "data_as_of": "2026-11-06T21:54:00",
  "query": {
    "lat": 32.7073,
    "lon": -117.1566,
    "arrival_time": "2026-11-08T19:00:00-08:00",
    "duration_minutes": 120,
    "radius_m": 400,
    "max_results": 10
  }
}
```

> **Frontend Implementation Tip**:
> When a `/v1/chat` response comes back:
> 1. Append `reply` into your chat bubble UI.
> 2. Pass `results` directly into your map component to refresh the map pins.
> 3. If `query.lat` and `query.lon` exist, center the map view around that target!

---

### 3.3 Meter Detail View: `GET /v1/meters/{pole_id}`
Use this when a user taps a specific parking pin on the map to open a details modal or slide-over panel.

#### Example Request:
```bash
curl "http://localhost:8000/v1/meters/7-724"
```

#### Response:
Contains full metadata, enforced hours schedule, and the weekly 15-minute bucket curve (ideal for rendering an hourly availability sparkline/barchart):
```json
{
  "meter": {
    "pole_id": "7-724",
    "lat": 32.7081,
    "lon": -117.1559,
    "zone": "Downtown",
    "area": "East Village",
    "sub_area": "700 SEVENTH AVE",
    "meter_type": "single_space",
    "rate_per_hour": 2.50,
    "max_minutes": 120,
    "enforced_hours": {
      "days": [1, 2, 3, 4, 5, 6],
      "day_names": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
      "start_time": "08:00",
      "end_time": "20:00"
    },
    "status": "active"
  },
  "weekly_profile": [
    { "dow": 1, "bucket": 32, "p_available": 0.85, "band": "likely", "mean_occupancy": 0.15 },
    { "dow": 1, "bucket": 48, "p_available": 0.52, "band": "possible", "mean_occupancy": 0.48 },
    { "dow": 1, "bucket": 56, "p_available": 0.22, "band": "unlikely", "mean_occupancy": 0.78 }
  ],
  "data_as_of": "2026-11-06T21:54:00"
}
```

---

### 3.4 Data Freshness & Health: `GET /v1/meta` and `GET /health`
- **`GET /health`**: Returns `{"status": "healthy", "ready": true, "meter_count": 3613}`. Use for uptime checks.
- **`GET /v1/meta`**: Returns `data_as_of` (e.g. `2026-11-06T21:54:00`). Display this in the footer or settings: *"Predictions calibrated with City of San Diego payment logs through Nov 6, 2026"*.

---

## 4. How the Frontend Should Render the Data

### 4.1 Map Pin Colors by Availability Band
Map each spot's `availability.band` and `status` to an intuitive color scheme:

| `status` | `availability.band` | Meaning | Recommended Pin Color | UI Badge Text |
|---|---|---|---|---|
| `metered` | `likely` ($p \ge 0.60$) | High chance of finding an open spot | **Green** (`#10B981`) | "Likely Open" |
| `metered` | `possible` ($0.30 \le p < 0.60$) | Moderate turnover / some congestion | **Yellow / Amber** (`#F59E0B`) | "Possible" |
| `metered` | `unlikely` ($p < 0.30$) | Typically packed at this time | **Red / Coral** (`#EF4444`) | "Unlikely" |
| `unmetered_now` | Any | Outside enforced hours (free parking) | **Blue / Slate** (`#3B82F6`) | "Free / Unenforced" |

### 4.2 Handling Over-Duration (`limit_exceeded: true`)
If a user asks for a stay longer than the meter permits (e.g. user wants 3 hours, meter is 2 hours max):
- The spot **is still returned** so the driver isn't left with 0 results.
- `limit_exceeded` will be `true`.
- Display a warning chip: ⚠️ *"Exceeds 2h limit — must move or re-park"*.

### 4.3 Mandatory User Disclaimers
Per City of San Diego data guidelines and the system spec, surface this footnote beneath results or in the info tooltip:
> *"Availability probabilities are historical estimates derived from City of San Diego payment transactions, not real-time parking sensors. Disabled placard holders and non-paying vehicles are not captured in payment feeds."*

---

## 5. TypeScript Interfaces

Copy and paste these type definitions directly into your frontend codebase (`types/parking.ts`):

```typescript
export type AvailabilityBand = 'likely' | 'possible' | 'unlikely';
export type ConfidenceLevel = 'high' | 'medium' | 'low';
export type MeterStatus = 'metered' | 'unmetered_now' | 'possibly_out_of_service';

export interface SpotAvailability {
  p_available: number | null; // null when unmetered_now
  band: AvailabilityBand;
  confidence: ConfidenceLevel;
  basis: 'pole' | 'sub_area' | 'enforcement_calendar' | 'default';
}

export interface ParkingSpot {
  pole_id: string;
  lat: number;
  lon: number;
  distance_m: number;
  walk_minutes: number;
  status: MeterStatus;
  availability: SpotAvailability;
  meter_type: 'single_space' | 'multi_space';
  rate_per_hour: number | null;
  max_minutes: number | null;
  limit_exceeded: boolean;
  note?: string;
  area?: string;
  sub_area?: string;
}

export interface SpotsQuery {
  lat: number;
  lon: number;
  arrival_time: string;
  duration_minutes?: number | null;
  radius_m: number;
  max_results: number;
}

export interface SpotsResponse {
  query: SpotsQuery;
  data_as_of: string;
  results: ParkingSpot[];
  notes: string[];
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  text: string;
}

export interface ChatRequest {
  messages: ChatMessage[];
  user_location?: {
    lat: number;
    lon: number;
  };
}

export interface ChatResponse {
  reply: string;
  results: ParkingSpot[];
  data_as_of?: string;
  query?: SpotsQuery;
}
```

---

## 6. Frontend Code Examples

### 6.1 React Hook Example (`useParkingSpots.ts`)

```typescript
import { useState, useCallback } from 'react';
import { ParkingSpot, SpotsResponse } from './types';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export function useParkingSpots() {
  const [spots, setSpots] = useState<ParkingSpot[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchSpots = useCallback(async (
    lat: number,
    lon: number,
    arrivalTime?: string,
    durationMinutes?: number
  ) => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({
        lat: lat.toString(),
        lon: lon.toString(),
        radius_m: '500',
        limit: '15'
      });
      if (arrivalTime) params.append('arrival_time', arrivalTime);
      if (durationMinutes) params.append('duration_minutes', durationMinutes.toString());

      const res = await fetch(`${API_BASE}/v1/spots?${params}`);
      if (!res.ok) {
        const errData = await res.json();
        throw new Error(errData.detail || 'Failed to fetch parking spots');
      }

      const data: SpotsResponse = await res.json();
      setSpots(data.results);
      return data;
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  return { spots, loading, error, fetchSpots };
}
```

### 6.2 React Chat Box Example (`useParkingChat.ts`)

```typescript
import { useState } from 'react';
import { ChatMessage, ChatResponse, ParkingSpot } from './types';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export function useParkingChat(onNewPins: (pins: ParkingSpot[]) => void) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(false);

  const sendMessage = async (userText: string, userLocation?: { lat: number; lon: number }) => {
    const updatedMessages: ChatMessage[] = [...messages, { role: 'user', text: userText }];
    setMessages(updatedMessages);
    setLoading(true);

    try {
      const res = await fetch(`${API_BASE}/v1/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          messages: updatedMessages,
          user_location: userLocation
        })
      });

      if (!res.ok) throw new Error('Chat assistant error');
      const data: ChatResponse = await res.json();

      setMessages(prev => [...prev, { role: 'assistant', text: data.reply }]);

      // Immediately plot returned spots onto the map
      if (data.results && data.results.length > 0) {
        onNewPins(data.results);
      }
    } catch (err) {
      setMessages(prev => [
        ...prev,
        { role: 'assistant', text: 'Sorry, I ran into an issue finding parking spots. Please try again.' }
      ]);
    } finally {
      setLoading(false);
    }
  };

  return { messages, loading, sendMessage };
}
```

---

## 7. Error Handling & Edge Cases

| Scenario | HTTP Status | Response Payload | Recommended Frontend Action |
|---|---|---|---|
| User clicks outside San Diego | `400 Bad Request` | `{"detail": "Coordinates (...) are outside San Diego coverage bounding box [...]"}` | Display toast: *"Coverage is limited to City of San Diego metered street parking."* |
| Searching late at night (e.g. 11 PM) | `200 OK` | `status: "unmetered_now"`, `p_available: null` | Display blue/gray pins: *"Street meters are free and unenforced at this hour."* |
| User exceeds rate limit on chat | `429 Too Many Requests` | `{"detail": "Rate limit exceeded..."}` | Show temporary cooldown notice (limit: 30 requests/min). |
| Server still booting or DB rebuilding | `503 Service Unavailable` | `{"detail": "Store metadata not loaded."}` | Show loading spinner with retry after 2 seconds. |

