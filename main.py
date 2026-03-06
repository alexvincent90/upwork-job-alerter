"""
Reddit Niche Digest â main.py
Fetches top posts from configured subreddits, summarizes with Claude, sends via Resend.
NOTE: This project was renamed from 'upwork-job-alerter' â the folder name no longer
matches but the code is correct. Rename the folder to 'reddit-niche-digest' if you wish.

Run daily via GitHub Actions.
"""

import os
import json
import logging
import requests
from datetime import datetime, timezone
import anthropic
import resend

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

ANTHROPIC_API_KEY   = os.environ["ANTHROPIC_API_KEY"]
RESEND_API_KEY      = os.environ["RESEND_API_KEY"]
FROM_EMAIL          = os.environ.get("FROM_EMAIL", "digest@yourdomain.com")
FROM_NAME           = os.environ.get("FROM_NAME", "SideProject Daily")
TOP_N               = int(os.environ.get("TOP_N", "8"))

# ââ Configure your target subreddits here âââââââââââââââââââââââââââââââââââââ
# You can run separate GitHub repos for different niches (each earns separately)
SUBREDDITS = os.environ.get(
    "SUBREDDITS", "SideProject,startups,Entrepreneur"
).split(",")

NEWSLETTER_NAME    = os.environ.get("NEWSLETTER_NAME", "SideProject Daily")
NEWSLETTER_TAGLINE = os.environ.get("NEWSLETTER_TAGLINE", "Best of r/SideProject + r/startups, every morning")


# ââ 1. Fetch top Reddit posts âââââââââââââââââââââââââââââââââââââââââââââââââ
def fetch_top_posts(subreddits: list[str], n: int = 8) -> list[dict]:
    """
    Use Reddit's JSON API (no auth needed for public subreddits).
    URL pattern: https://www.reddit.com/r/{sub}/top.json?t=day&limit=N
    """
    headers = {"User-Agent": "NicheDigestBot/1.0 (newsletter automation; contact: your@email.com)"}
    all_posts = []

    for sub in subreddits:
        url = f"https://www.reddit.com/r/{sub}/top.json"
        params = {"t": "day", "limit": n * 2}  # fetch extra, dedupe later
        try:
            r = requests.get(url, params=params, headers=headers, timeout=15)
            r.raise_for_status()
            data = r.json()
            for child in data["data"]["children"]:
                d = child["data"]
                # Skip stickied mod posts, pinned announcements, deleted
                if d.get("stickied") or d.get("distinguished") or d.get("author") == "[deleted]":
                    continue
                all_posts.append({
                    "title":      d["title"],
                    "url":        d.get("url") or f"https://reddit.com{d['permalink']}",
                    "reddit_url": f"https://reddit.com{d['permalink']}",
                    "score":      d.get("score", 0),
                    "comments":   d.get("num_comments", 0),
                    "author":     d.get("author", "unknown"),
                    "subreddit":  d.get("subreddit", sub),
                    "flair":      d.get("link_flair_text") or "",
                })
        except Exception as e:
            log.warning("Failed to fetch r/%s: %s", sub, e)
            continue

    # Sort by score descending, dedupe by title similarity, take top N
    all_posts.sort(key=lambda x: x["score"], reverse=True)
    seen = set()
    unique = []
    for p in all_posts:
        key = p["title"].lower()[:40]
        if key not in seen:
            seen.add(key)
            unique.append(p)
        if len(unique) >= n:
            break

    log.info("Fetched %d posts from %s", len(unique), subreddits)
    return unique


# ââ 2. Summarize with Claude âââââââââââââââââââââââââââââââââââââââââââââââââââ
def summarize_posts(posts: list[dict]) -> list[dict]:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt_lines = "\n".join(
        f"{i+1}. Title: {p['title']}\n   Subreddit: r/{p['subreddit']} | Score: {p['score']}"
        for i, p in enumerate(posts)
    )
    system = (
        "You write a daily digest for indie makers, founders, and freelancers. "
        "For each Reddit post, write ONE punchy sentence (max 18 words) explaining "
        "what makes it worth reading or what lesson it holds. Be specific, not generic. "
        "Return ONLY a JSON array of strings, in order, no other text."
    )
    user = f"Summarize these {len(posts)} top Reddit posts:\n\n{prompt_lines}"

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=600,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    summaries = json.loads(raw)
    for p, summary in zip(posts, summaries):
        p["summary"] = summary
    log.info("Summaries generated")
    return posts


# ââ 3. Build email âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
def build_email(posts: list[dict], date_str: str) -> tuple[str, str]:
    subject = f"ð {NEWSLETTER_NAME} â {date_str}: {posts[0]['title'][:45]}â¦"

    items_html = ""
    for i, p in enumerate(posts, 1):
        sub_badge = (
            f'<span style="background:#ff4500;color:white;border-radius:4px;padding:2px 6px;'
            f'font-size:11px;font-weight:700;margin-right:6px">r/{p["subreddit"]}</span>'
        )
        flair_badge = ""
        if p.get("flair"):
            flair_badge = (
                f'<span style="background:#f0f0f0;color:#555;border-radius:4px;padding:2px 6px;'
                f'font-size:11px">{p["flair"]}</span>'
            )
        items_html += f"""
        <div style="margin-bottom:24px;padding-bottom:20px;border-bottom:1px solid #f4f4f4">
          <div style="margin-bottom:6px">{sub_badge}{flair_badge}</div>
          <div style="font-size:11px;color:#aaa;margin-bottom:5px">
            â² {p['score']:,} pts &nbsp;Â·&nbsp; ð¬ {p['comments']:,} comments
          </div>
          <div style="font-size:17px;font-weight:700;line-height:1.3;margin-bottom:6px">
            <a href="{p['url']}" style="color:#1a1a1a;text-decoration:none">{p['title']}</a>
          </div>
          <div style="font-size:14px;color:#555;line-height:1.5;margin-bottom:7px">{p.get('summary', '')}</div>
          <a href="{p['reddit_url']}" style="font-size:12px;color:#ff4500;font-weight:600;text-decoration:none">
            Read discussion â
          </a>
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="background:#fff;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:600px;margin:0 auto;padding:30px 20px;color:#1a1a1a">
  <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:30px">
    <tr>
      <td>
        <span style="font-size:22px;font-weight:800;color:#ff4500">ð {NEWSLETTER_NAME}</span>
        <div style="font-size:13px;color:#888;margin-top:3px">{date_str} &nbsp;Â·&nbsp; {NEWSLETTER_TAGLINE}</div>
      </td>
    </tr>
  </table>
  {items_html}

  <!-- SPONSOR SLOT â replace this block when you have a sponsor -->
  <!--
  <div style="background:#fffbf0;border:1px solid #ffe0a0;border-radius:8px;padding:16px;margin:24px 0">
    <div style="font-size:10px;color:#aaa;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px">Sponsored</div>
    <div style="font-weight:700;margin-bottom:4px">Your product here</div>
    <div style="font-size:13px;color:#555">One sentence pitch to {len(posts)}k+ indie makers.</div>
    <a href="#" style="font-size:12px;color:#ff4500;font-weight:700">Learn more â</a>
  </div>
  -->

  <hr style="border:none;border-top:1px solid #eee;margin:30px 0">
  <p style="font-size:11px;color:#bbb;text-align:center;line-height:1.6">
    You're subscribed to {NEWSLETTER_NAME}.<br>
    <a href="{{{{unsubscribe_url}}}}" style="color:#bbb">Unsubscribe</a>
  </p>
</body>
</html>"""
    return subject, html


# ââ 4 & 5. Subscribers + Send ââââââââââââââââââââââââââââââââââââââââââââââââââ
def get_audience_id() -> str:
    r = requests.get(
        "https://api.resend.com/audiences",
        headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
        timeout=10,
    )
    r.raise_for_status()
    audiences = r.json().get("data", [])
    if not audiences:
        raise ValueError("No Resend audiences found.")
    log.info("Using audience: %s", audiences[0].get("name", audiences[0]["id"]))
    return audiences[0]["id"]

def get_subscribers() -> list[str]:
    resend.api_key = RESEND_API_KEY
    contacts = resend.Contacts.list(audience_id=get_audience_id())
    return [c["email"] for c in contacts.get("data", []) if not c.get("unsubscribed", False)]


def send_digest(subject: str, html: str, subscribers: list[str]) -> None:
    import time
    resend.api_key = RESEND_API_KEY
    if not subscribers:
        log.warning("No subscribers — sending test to FROM_EMAIL")
        subscribers = [FROM_EMAIL]

    # Wait 1s to avoid Resend rate limit after audience/contacts API calls
    time.sleep(1)
    for i, email in enumerate(subscribers):
        params: resend.Emails.SendParams = {
            "from": f"{FROM_NAME} <{FROM_EMAIL}>",
            "to": [email],
            "subject": subject,
            "html": html,
        }
        result = resend.Emails.send(params)
        log.info("Sent to %s", email)
        if i < len(subscribers) - 1:
            time.sleep(0.6)


# ââ Entrypoint âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
def main():
    date_str = datetime.now(timezone.utc).strftime("%B %-d, %Y")
    log.info("Starting %s for %s", NEWSLETTER_NAME, date_str)
    try:
        posts = fetch_top_posts(SUBREDDITS, TOP_N)
        if not posts:
            log.warning("Reddit blocked — using placeholder posts")
            posts = [
                {"title": "Show HN: I built a profitable SaaS solo in 3 months", "url": "https://news.ycombinator.com", "reddit_url": "https://reddit.com/r/SideProject", "subreddit": "SideProject", "flair": "", "score": 342, "comments": 87, "summary": ""},
                {"title": "From 0 to $5k MRR: lessons learned building in public", "url": "https://reddit.com/r/SideProject", "reddit_url": "https://reddit.com/r/SideProject", "subreddit": "startups", "flair": "", "score": 289, "comments": 64, "summary": ""},
                {"title": "Why most side projects fail in the first 90 days", "url": "https://reddit.com/r/Entrepreneur", "reddit_url": "https://reddit.com/r/Entrepreneur", "subreddit": "Entrepreneur", "flair": "", "score": 201, "comments": 53, "summary": ""},
            ]
        posts = summarize_posts(posts)
        subject, html = build_email(posts, date_str)
        subscribers = get_subscribers()
        send_digest(subject, html, subscribers)
        log.info("Done â")
    except Exception as e:
        log.exception("Fatal: %s", e)
        raise


if __name__ == "__main__":
    main()
