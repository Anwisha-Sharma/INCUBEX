/**
 * RescueMesh Tier 1 Standalone Local WebSocket Hub
 * Run on any laptop, Raspberry Pi, or local router with Node.js installed.
 * Completely offline: zero cloud or internet connection required.
 *
 * Usage:
 *   node server.js [port]
 */

const http = require("http");
const { WebSocketServer } = require("ws");

const PORT = process.env.PORT || process.argv[2] || 8080;

// In-memory local gossip cache
const localEvents = new Map(); // event_id -> event_json_object

const server = http.createServer((req, res) => {
  if (req.url === "/health") {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ status: "ok", role: "rescuemesh-local-hub", events_cached: localEvents.size }));
    return;
  }
  res.writeHead(200, { "Content-Type": "text/plain" });
  res.end("RescueMesh Local WebSocket Hub Running. Connect to ws://<this-ip>:" + PORT + "/ws/sync\n");
});

const wss = new WebSocketServer({ server, path: "/ws/sync" });

console.log(`[RescueMesh Hub] Starting on port ${PORT}...`);

wss.on("connection", (ws, req) => {
  const remoteIp = req.socket.remoteAddress;
  console.log(`[RescueMesh Hub] Peer connected from ${remoteIp}`);

  ws.on("message", (rawMessage) => {
    try {
      const msg = JSON.parse(rawMessage.toString());
      const type = (msg.type || "").toUpperCase();

      if (type === "HELLO") {
        const peerKnown = new Set(msg.known_event_ids || []);
        const serverKnown = new Set(localEvents.keys());

        const serverMissingForPeer = [...serverKnown].filter((id) => !peerKnown.has(id));
        const peerCanUpload = [...peerKnown].filter((id) => !serverKnown.has(id));

        ws.send(
          JSON.stringify({
            type: "EVENTS_OFFER",
            server_missing_for_peer: serverMissingForPeer,
            peer_can_upload: peerCanUpload,
            total_server_events: localEvents.size,
          })
        );
      } else if (type === "GET_EVENTS") {
        const requestedIds = msg.event_ids || [];
        const records = requestedIds.map((id) => localEvents.get(id)).filter(Boolean);
        ws.send(JSON.stringify({ type: "EVENTS_BATCH", events: records }));
      } else if (type === "PUBLISH_EVENTS") {
        const events = msg.events || [];
        const newEvents = [];

        for (const ev of events) {
          if (ev && ev.event_id && !localEvents.has(ev.event_id)) {
            localEvents.set(ev.event_id, ev);
            newEvents.push(ev);
          }
        }

        ws.send(
          JSON.stringify({
            type: "PUBLISH_ACK",
            ingested_count: newEvents.length,
            total_received: events.length,
          })
        );

        // Gossip broadcast newly discovered events to all other connected peers on the hotspot
        if (newEvents.length > 0) {
          const broadcastPayload = JSON.stringify({
            type: "EVENTS_BROADCAST",
            events: newEvents,
          });

          for (const client of wss.clients) {
            if (client !== ws && client.readyState === 1 /* OPEN */) {
              client.send(broadcastPayload);
            }
          }
        }
      } else if (type === "PING") {
        ws.send(JSON.stringify({ type: "PONG" }));
      }
    } catch (err) {
      ws.send(JSON.stringify({ type: "ERROR", message: "Invalid payload: " + err.message }));
    }
  });

  ws.on("close", () => {
    console.log(`[RescueMesh Hub] Peer disconnected from ${remoteIp}`);
  });
});

server.listen(PORT, "0.0.0.0", () => {
  console.log(`[RescueMesh Hub] Listening at http://0.0.0.0:${PORT} and ws://0.0.0.0:${PORT}/ws/sync`);
  console.log(`[RescueMesh Hub] Share your hotspot IP address with other devices to start sync.`);
});
