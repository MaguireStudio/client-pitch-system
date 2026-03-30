from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import os
import requests

app = FastAPI()

app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_credentials=True, allow_methods=['*'], allow_headers=['*'])

# Define SHEET_API_URL from environment variable with fallback
SHEET_API_URL = os.getenv('SHEET_API_URL', '')

@app.get('/', response_class=HTMLResponse)
async def serve_index():
    try:
        with open('public/index.html', 'r') as f:
            return f.read()
    except Exception as e:
        return f'<h1>Error Loading Page</h1><p>{{str(e)}}</p>'

@app.get('/dashboard', response_class=HTMLResponse)
async def serve_dashboard():
    try:
        with open('public/dashboard.html', 'r') as f:
            return f.read()
    except Exception as e:
        return f'<h1>Error Loading Dashboard</h1><p>{{str(e)}}</p>'

@app.post('/api/leads')
async def capture_lead(request: Request):
    try:
        lead_data = await request.json()
        if not SHEET_API_URL:
            return {'status': 200, 'message': 'Lead captured (offline mode)', 'data': lead_data}
        response = requests.post(SHEET_API_URL, json=lead_data)
        return {'status': response.status_code, 'data': response.json() if response.ok else response.text}
    except Exception as e:
        return {'status': 500, 'error': str(e)}

@app.get('/api/leads')
async def fetch_leads():
    try:
        if not SHEET_API_URL:
            return {'status': 200, 'leads': [], 'message': 'No API configured - offline mode'}
        response = requests.get(SHEET_API_URL)
        return {'status': response.status_code, 'leads': response.json() if response.ok else []}
    except Exception as e:
        return {'status': 500, 'error': str(e), 'leads': []}