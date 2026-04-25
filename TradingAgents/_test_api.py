import requests, json
s = requests.Session()
s.trust_env = False

# Test homepage
r = s.get('http://localhost:5000/', timeout=10)
print('Homepage:', r.status_code)

# Test analyze with new features
r = s.get('http://localhost:5000/api/analyze?code=300124', timeout=30)
print('Status:', r.status_code)
if r.status_code == 200:
    data = r.json()
    print('Name:', data.get('name'))
    a = data.get('analysis', {})
    print('Score:', a.get('score'), '|', a.get('action_text'))
    adv = a.get('beginner_advice', {})
    print()
    print('--- Beginner Advice ---')
    print('Summary:', adv.get('summary', '')[:80])
    print('Position:', adv.get('position', '')[:80])
    print('Timing:', adv.get('timing', '')[:80])
    print('Stoploss:', adv.get('stoploss', '')[:80])
    print('Risk:', adv.get('risk', '')[:80])
    print('Level:', adv.get('level', ''))
    bt = a.get('buy_timing', [])
    print()
    print('--- Buy Timing ---')
    for t in bt:
        desc = t['description'][:60]
        cond = t['condition']
        price = t.get('target_price', '-')
        print('  ' + cond + ': ' + str(price) + ' | ' + desc)
    bsp = a.get('buy_sell_points', [])
    print()
    print('Recent signals:', len(bsp))
    if bsp:
        print('Last:', bsp[-1])
else:
    print(r.text[:500])
