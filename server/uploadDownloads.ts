import express from 'express';

// A missing attachment must never fall through to the HTML SPA fallback.
export function uploadDownloads(directory: string) {
  const router = express.Router();
  router.use(express.static(directory, {
    index: false,
    redirect: false,
    setHeaders(res) {
      res.setHeader('Content-Type', 'application/octet-stream');
      res.setHeader('Content-Disposition', 'attachment');
      res.setHeader('X-Content-Type-Options', 'nosniff');
    },
  }));
  router.use((_req, res) => {
    res.status(404).json({ error: 'Anhang nicht gefunden. Bitte erneut hochladen.' });
  });
  return router;
}
