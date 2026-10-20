from fastapi.testclient import TestClient
import app.main as api

client = TestClient(api.app)

def register(email):
    response = client.post('/api/register', json={'email': email, 'password': 'CorrectHorseBattery!42'})
    assert response.status_code == 201
    return response.json()['access_token']

def test_health_and_registration_login(tmp_path, monkeypatch):
    monkeypatch.setattr(api, 'DB_PATH', str(tmp_path / 'users.db'))
    assert client.get('/health').json() == {'status': 'ok'}
    token = register('one@example.com')
    assert client.post('/api/login', json={'email':'one@example.com','password':'CorrectHorseBattery!42'}).status_code == 200
    assert client.post('/api/login', json={'email':'one@example.com','password':'WrongPassword!42'}).status_code == 401
    assert client.post('/api/register', json={'email':'one@example.com','password':'CorrectHorseBattery!42'}).status_code == 409
    assert client.get('/api/stocks', headers={'Authorization':f'Bearer {token}'}).json() == []

def test_stock_crud_is_user_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(api, 'DB_PATH', str(tmp_path / 'stocks.db'))
    token1 = register('user1@example.com')
    token2 = register('user2@example.com')
    h1, h2 = {'Authorization':f'Bearer {token1}'}, {'Authorization':f'Bearer {token2}'}
    created = client.post('/api/stocks', headers=h1, json={'symbol':'reliance','exchange':'NSE','purchase_price':100,'previous_close':100})
    assert created.status_code == 201
    stock_id = created.json()['id']
    assert client.get('/api/stocks', headers=h2).json() == []
    assert client.post(f'/api/stocks/{stock_id}/sale', headers=h2, json={'actual_sell_price':110}).status_code == 404
    assert client.post(f'/api/stocks/{stock_id}/sale', headers=h1, json={'actual_sell_price':110}).json()['actual_sell_price'] == 110
    assert client.get('/api/stocks', headers=h1).json()[0]['actual_sell_price'] == 110
    assert client.delete(f'/api/stocks/{stock_id}', headers=h2).status_code == 404
    assert client.delete(f'/api/stocks/{stock_id}', headers=h1).status_code == 204

def test_portfolio_limit_and_duplicate(tmp_path, monkeypatch):
    monkeypatch.setattr(api, 'DB_PATH', str(tmp_path / 'limit.db'))
    token = register('limit@example.com')
    headers = {'Authorization':f'Bearer {token}'}
    first = client.post('/api/stocks', headers=headers, json={'symbol':'ABC','exchange':'NSE'})
    assert first.status_code == 201
    assert client.post('/api/stocks', headers=headers, json={'symbol':'ABC','exchange':'NSE'}).status_code == 409
    for i in range(1, 40):
        response = client.post('/api/stocks', headers=headers, json={'symbol':f'S{i}','exchange':'BSE'})
        assert response.status_code == 201
    assert len(client.get('/api/stocks', headers=headers).json()) == 40
    assert client.post('/api/stocks', headers=headers, json={'symbol':'EXTRA','exchange':'NSE'}).status_code == 400

def test_invalid_or_missing_auth_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(api, 'DB_PATH', str(tmp_path / 'auth.db'))
    assert client.get('/api/stocks').status_code == 401
    assert client.get('/api/stocks', headers={'Authorization':'Bearer broken.token'}).status_code == 401
