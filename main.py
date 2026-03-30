from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import requests

app = FastAPI()

app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_credentials=True, allow_methods=['*'], allow_headers=['*'])

@app.get('/', response_class=HTMLResponse)
async def serve_index():
    return open('public/index.html').read()

@app.get('/dashboard', response_class=HTMLResponse)
async def serve_dashboard():
    return open('public/dashboard.html').read()

@app.post('/api/leads')
async def capture_lead(request: Request):
    lead_data = await request.json()
    response = requests.post(SHEET_API_URL, json=lead_data)
    return {'status': response.status_code, 'data': response.json()}

@app.get('/api/leads')
async def fetch_leads():
    response = requests.get(SHEET_API_URL)
    return {'status': response.status_code, 'leads': response.json()}

if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=8000)