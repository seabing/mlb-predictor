import requests

nym = requests.get('http://127.0.0.1:8001/roster/NYM').json()['roster']
ari = requests.get('http://127.0.0.1:8001/roster/ARI').json()['roster']

resp = requests.post('http://127.0.0.1:8001/predict', json={
    'home_roster': nym,
    'away_roster': ari,
    'home_pitcher_id': 642547,
    'away_pitcher_id': 621244
})

print('Status:', resp.status_code)
print('Response:', resp.text)