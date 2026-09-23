# Frontend (Lovable)

## 1. Build the UI
1. Create a new Lovable project.
2. Paste the prompt from `LOVABLE_PROMPT.md`.
3. Paste `api-types.ts` into `src/lib/api-types.ts`.
4. Iterate in Lovable if something is off, for example: "source cards should wrap on mobile".

## 2. Expose the local API to Lovable
The Lovable preview and published app run on `https://*.lovable.app` / `*.lovableproject.com`, which is the public internet. They cannot reach `http://localhost:8000` on your machine, so use a tunnel:

```powershell
# option A: ngrok (free account + authtoken once: ngrok config add-authtoken <token>)
ngrok http 8000
# option B: Cloudflare quick tunnel (no account)
cloudflared tunnel --url http://localhost:8000
```
Copy the `https://…` URL and set it in the app's **Settings → API base URL**. It is stored in the browser, so a new tunnel URL needs no redeploy. Click **Test connection** to confirm.

## 3. CORS
The backend already allows:
- the origins in `FRONTEND_ORIGIN` (comma-separated; defaults to localhost:3000 and 5173)
- any origin matching `FRONTEND_ORIGIN_REGEX`, which defaults to `https://*.lovable.app`, `*.lovableproject.com`, and `*.lovable.dev`

If you publish on a custom domain, add it to `FRONTEND_ORIGIN`.

## 4. ngrok browser warning
Free ngrok shows an interstitial page for browser requests. The API client sends the `ngrok-skip-browser-warning: true` header on every request, which skips it. The prompt already asks Lovable to add this header.

## 5. Admin token
Document upload, status scenarios, and evaluation runs need the `EVALS_ADMIN_TOKEN` value from your `.env`. Enter it in Settings. It is only stored in your browser.

## 6. Troubleshooting
| Symptom | Fix |
| --- | --- |
| "Failed to fetch" / CORS error | Check that the tunnel is running and the base URL has no trailing path. For a custom domain, add it to `FRONTEND_ORIGIN` and restart the API. |
| HTML instead of JSON | The ngrok warning page is showing: make sure the `ngrok-skip-browser-warning` header is sent. |
| 403 on a thread | Expected: that thread belongs to another demo user (isolation). |
| Health dot amber | Open Settings → Test connection and see which dependency is failing (for example, the model key is missing). |
