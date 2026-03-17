#!/usr/bin/env python3
"""
Simple HTTP command server for remote execution.
Endpoints:
- POST /exec - Execute shell command
- GET /health - Health check
"""
from flask import Flask, request, jsonify
import subprocess
import os

app = Flask(__name__)

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'gpu': os.path.exists('/dev/nvidia0')})

@app.route('/exec', methods=['POST'])
def exec_cmd():
    cmd = request.json.get('cmd', '')
    if not cmd:
        return jsonify({'error': 'No command provided'}), 400
    
    # Run the command
    result = subprocess.run(
        cmd, 
        shell=True, 
        capture_output=True, 
        text=True,
        timeout=300  # 5 min timeout
    )
    return jsonify({
        'stdout': result.stdout,
        'stderr': result.stderr,
        'returncode': result.returncode
    })

if __name__ == '__main__':
    # Run on all interfaces
    app.run(host='0.0.0.0', port=8080, threaded=True)
