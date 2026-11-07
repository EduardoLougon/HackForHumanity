# Render Deployment Guide — San Diego Parking Finder Backend

This guide walks you step-by-step through deploying the backend API to **Render** using Docker.

---

## 1. Prerequisites

1. A [Render Account](https://render.com) (free or paid).
2. Your repository pushed to GitHub.
3. A **Google Gemini API Key** (get one free at [aistudio.google.com](https://aistudio.google.com)).

---

## 2. Deployment Method 1: Using Render Blueprint (Recommended — 2 Minutes)

This repository includes a [`render.yaml`](file:///c:/Users/dudul/OneDrive/Documentos/GitHub/HackForHumanity/render.yaml) file that automatically configures everything for you.

1. **Push your code to GitHub**:
   ```bash
   git add .
   git commit -m "Complete San Diego Parking Backend"
   git push origin main
   ```
2. Open the [Render Dashboard](https://dashboard.render.com).
3. Click **New +** (top right) and select **Blueprint**.
4. Connect your GitHub account and choose the `HackForHumanity` repository.
5. Render will automatically read `render.yaml` and prompt you for the environment variables:
   - **`GEMINI_API_KEY`**: Paste your Google Gemini API key.
6. Click **Apply**.
7. Render will build the Docker container and deploy the service.

---

## 3. Deployment Method 2: Manual Web Service Setup

If you prefer setting it up manually in the Render UI:

1. In the Render Dashboard, click **New +** > **Web Service**.
2. Select **Build and deploy from a Git repository**.
3. Choose your repository.
4. Configure the settings:
   - **Name**: `san-diego-parking-backend` (or your preferred name)
   - **Region**: Oregon (US West) or Ohio (US East)
   - **Branch**: `main`
   - **Language / Environment**: `Docker`
   - **Instance Type**: `Free` or `Starter` ($7/mo recommended for zero cold-starts and sustained memory)
5. Under **Advanced Settings**:
   - **Health Check Path**: `/health`
   - **Environment Variables**:
     | Key | Value | Notes |
     |---|---|---|
     | `GEMINI_API_KEY` | `AIzaSy...` | Your Google Gemini API Key |
     | `GEMINI_MODEL` | `gemini-2.5-flash` | Fast, low-latency model |
     | `GEOCODER_PROVIDER`| `nominatim` | Free OSM geocoding |
     | `CORS_ORIGINS` | `*` | Or specify your frontend URL (e.g. `https://my-app.vercel.app`) |
6. Click **Create Web Service**.

---

## 4. How the Docker Container Runs on Render

- **Port Binding**: Render automatically assigns an arbitrary internal port using the `$PORT` environment variable (e.g. `10000`).
  Our `Dockerfile` automatically detects and binds to it:
  ```dockerfile
  CMD uvicorn src.parking.api.main:app --host 0.0.0.0 --port ${PORT:-8000}
  ```
- **Serving Database**: The pre-built serving database (`data/serving/parking.duckdb`, 35 MB) is packaged directly into the Docker image, so Render boots instantly without having to download 750 MB of raw CSVs or spend 10 minutes building grids.
- **Startup Time**: Cold container start is under 3 seconds!

---

## 5. Verifying Your Deployment

Once Render finishes deploying and the status turns **Live**:

1. **Check Health**:
   Visit: `https://<your-service-name>.onrender.com/health`
   Expected response:
   ```json
   {
     "status": "healthy",
     "ready": true,
     "database_connected": true,
     "meter_count": 3613
   }
   ```

2. **Check Swagger Docs**:
   Visit: `https://<your-service-name>.onrender.com/docs`
   You can test endpoints interactively directly from your browser!

3. **Test Spots Search**:
   Visit: `https://<your-service-name>.onrender.com/v1/spots?lat=32.7073&lon=-117.1566&limit=5`

---

## 6. Connecting Your Frontend

In your frontend project (Next.js, Vite, React, Flutter, etc.):

1. Add your Render backend URL to your frontend `.env`:
   ```bash
   NEXT_PUBLIC_API_URL=https://<your-service-name>.onrender.com
   ```
2. In the Render Dashboard under **Environment Variables**, update `CORS_ORIGINS`:
   ```bash
   CORS_ORIGINS=https://<your-frontend-domain>.vercel.app,http://localhost:3000
   ```

