#!/usr/bin/env python3
"""Explicit offline browser fixture. Never a real-model quality demonstration."""
from __future__ import annotations

import argparse
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pocketchat.config import load_config, profile_config
from pocketchat.office import OfficeWorkspace
from pocketchat.office_web import create_server


class FixtureProvider(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.requests.append(data)
        prompt = data['messages'][-1]['content']
        if 'Office request' in prompt:
            envelope = json.loads(prompt.split('\n', 1)[1])
            if 'previous_report' in envelope:
                text = '# Weekly report\n\nCompleted 12 customer follow-ups [S1].\n\n## Next step\nPrepare the handover.'
            else:
                text = '# Weekly report\n\n## Progress\nCompleted **12** customer follow-ups [S1].\n\n## Risks\nThe supplied notes do not specify a budget.\n\n## Next steps\n- Prepare the handover.\n- Confirm the next reporting date.'
        else:
            text = 'Hello! We can think through your work together. Add a few notes whenever you want to make a report.'
        payload = {'choices':[{'message':{'role':'assistant','content':text},'finish_reason':'stop'}], 'model':'offline-fixture', 'usage':{'prompt_tokens':120,'completion_tokens':55}}
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    root = args.output
    root.mkdir(mode=0o700)  # An existing workspace is never silently reused.
    provider = ThreadingHTTPServer(('127.0.0.1',0),FixtureProvider)
    provider.requests = []
    threading.Thread(target=provider.serve_forever,daemon=True).start()
    config_file = root / 'fixture-config.json'
    config_file.write_text(json.dumps(dict(schema_version='2',name='offline-office-fixture',default_profile='fixture',profiles={'fixture':dict(base_url='http://127.0.0.1:%d/v1'%provider.server_port,model='offline-fixture',api_key_env='OFFICE_UNUSED',requires_api_key=False,timeout_seconds=10,max_context_chars=60000)})))
    app = OfficeWorkspace(root/'workspace',profile_config(load_config(config_file)))
    server = create_server(app)
    print(json.dumps(dict(origin=server.origin,token=server.bootstrap_token,fixture=True)),flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.close()
        provider.shutdown()
        provider.server_close()
        (root/'provider-requests.json').write_text(json.dumps(provider.requests,indent=2))


if __name__ == '__main__':
    main()
