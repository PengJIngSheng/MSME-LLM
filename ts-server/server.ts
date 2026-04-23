import express from 'express';
import cors from 'cors';
import { createProxyMiddleware } from 'http-proxy-middleware';
import path from 'path';

const app = express();
const PORT = process.env.PORT || 3000;
const PYTHON_BACKEND_URL = 'http://127.0.0.1:8000';

app.use(cors());

// A test route entirely handled by TypeScript
app.get('/api/ts-ping', (req, res) => {
    res.json({ message: 'Hello from the new TypeScript Server!', timestamp: new Date().toISOString() });
});

// Proxy all other /api calls to the Python backend
app.use('/api', createProxyMiddleware({
    target: PYTHON_BACKEND_URL,
    changeOrigin: true,
    // We don't parse body here because http-proxy needs the raw stream to forward properly
}));

// Serve the frontend static files
const staticPath = path.join(__dirname, '../static');
app.use('/static', express.static(staticPath));

// Fallback to index.html for the root route
app.get('/', (req, res) => {
    res.sendFile(path.join(staticPath, 'index.html'));
});

app.listen(PORT, () => {
    console.log(`🚀 TypeScript Server running on http://localhost:${PORT}`);
    console.log(`📡 Proxying unhandled API requests to Python Server at ${PYTHON_BACKEND_URL}`);
});
