import asyncio, sys, os, json
sys.path.insert(0, '.')

from dotenv import load_dotenv
load_dotenv('.env')

ws_email = os.environ.get('WS_EMAIL', '')
print('WS_EMAIL set:', bool(ws_email), flush=True)

from mcp_server import get_profile, get_portfolio, get_xray, get_concentration_warnings

async def main():
    print('\n=== 1. get_profile ===', flush=True)
    try:
        result = await get_profile()
        print(result, flush=True)
    except Exception as e:
        print(f'ERROR: {e}', flush=True)

    print('\n=== 2. get_portfolio ===', flush=True)
    try:
        result = await get_portfolio()
        print(result, flush=True)
    except Exception as e:
        print(f'ERROR: {e}', flush=True)

    print('\n=== 3. get_xray ===', flush=True)
    try:
        result = await get_xray()
        print(result, flush=True)
    except Exception as e:
        print(f'ERROR: {e}', flush=True)

    print('\n=== 4. get_concentration_warnings ===', flush=True)
    try:
        result = await get_concentration_warnings()
        print(result, flush=True)
    except Exception as e:
        print(f'ERROR: {e}', flush=True)

asyncio.run(main())
