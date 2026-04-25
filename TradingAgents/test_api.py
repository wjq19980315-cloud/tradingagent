import os
import requests

os.environ['CLAWSOCKET_API_KEY'] = 'sk-EVF8bQ4mTxk1m3uHNLedGjlOMvbAuS1pvyYLBvUt991ocl8x'

url = 'https://api.clawsocket.com/v1/models'
headers = {
    'Authorization': f'Bearer {os.environ.get("CLAWSOCKET_API_KEY")}'
}
resp = requests.get(url, headers=headers)
print(resp.status_code)
print(resp.text)