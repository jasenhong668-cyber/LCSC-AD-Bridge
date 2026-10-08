"""Windowless worker started by AD's documented RunApplication function."""
import argparse, datetime, pathlib, sys, traceback
import bridge

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--job')
    args = parser.parse_args()
    root = pathlib.Path(sys.executable).parent if getattr(sys, 'frozen', False) else pathlib.Path(__file__).resolve().parent
    with (root / 'ad-launcher.log').open('a', encoding='utf-8') as log:
        sys.stdout = sys.stderr = log
        print(datetime.datetime.now().isoformat(), 'AD job starting')
        try: return bridge.run_ad_job(root / 'launcher-config.json', args.job)
        except Exception:
            traceback.print_exc()
            return 1

if __name__ == '__main__': sys.exit(main())
