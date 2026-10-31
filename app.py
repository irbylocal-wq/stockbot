
from flask import Flask, jsonify

app = Flask(__name__)

@app.get("/")
def home():
    return """
    <h1>iPOSG Stock Bot</h1>
    <p>Web application is running.</p>
    <p>Inventory automation is not yet connected.</p>
    """

@app.get("/api/health")
def health():
    return jsonify({
        "status": "online",
        "service": "iposg-stockbot"
    })
