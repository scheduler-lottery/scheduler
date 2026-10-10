# Asking Northwestern for a Canvas API key

With a developer key from Northwestern's Canvas team, the class-list step becomes:

1. **Connect Canvas**.
2. Sign in, only if needed, on Canvas's own page.
3. **Authorize**.
4. Pick the course.
5. **Add**.

There's nothing to install, and it works the same in every browser. Scheduler already
has this built and switched off (`CANVAS_OAUTH_*`, see below). The key just turns it on.

Northwestern says it won't consider integration requests from individual instructors,
so the request needs a unit sponsor, ideally Pritzker Law. Send it to the Canvas team
and copy Law IT.

## The email

**To:** canvas@northwestern.edu
**Cc:** lit@law.northwestern.edu
**Subject:** Request: read-only, scoped Canvas API developer key for a class sign-up tool (Pritzker Law)

> Hello,
>
> I teach at Pritzker and built Scheduler (https://scheduler-lottery.vercel.app), a
> small site that runs presentation-day sign-ups for a class: students rank the days
> they could present, and a fair lottery assigns them. To know who is in the class, it
> needs each course's student names and email addresses. Today professors bring these
> over by hand from Canvas.
>
> I'd like to request an OAuth2 API developer key so a professor can authorize
> Scheduler to read just that, for their own course, with Canvas's standard consent
> screen. The key would be:
>
> - **Scoped and read-only.** "Enforce scopes" on, with exactly two scopes:
>   `url:GET|/api/v1/courses` and `url:GET|/api/v1/courses/:course_id/users`.
>   "Allow include parameters" can stay off.
> - **One redirect URI:** https://scheduler-lottery.vercel.app/teach/canvas/oauth/callback
> - **Used once per import.** Scheduler keeps only each student's name and email. It
>   deletes the access token as soon as the list is read (DELETE /login/oauth2/token)
>   and stores no tokens or refresh tokens.
> - **Hosted** on Vercel, with data in Supabase (Postgres), encrypted in transit and at
>   rest. Each professor sees only their own sheets. The code is public:
>   https://github.com/scheduler-lottery/scheduler
>
> I'm happy to complete the Service Provider Security Assessment, sign an Information
> Security Agreement with FERPA provisions, and meet to walk through it. If an LTI 1.3
> tool with Names and Role Provisioning would be easier for you to approve than an
> OAuth app, I can build that instead.
>
> Could you tell me which process applies, and whether Pritzker Law should sponsor
> the request?
>
> Thank you,
> Nathan Reitinger

## Once the key arrives

In Vercel, open **Project → Settings → Environment Variables**, add these, and
redeploy:

| Name | Value |
| --- | --- |
| `CANVAS_OAUTH_HOST` | `canvas.northwestern.edu` |
| `CANVAS_OAUTH_CLIENT_ID` | the key's id (a long number) |
| `CANVAS_OAUTH_CLIENT_SECRET` | the key's secret (keep it only in Vercel, never in the code) |

The class-list step then offers **Connect Canvas** first, in every browser.
