import os
import random
import secrets
from flask import Flask, render_template_string, request, redirect, url_for, session
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)

# --- MODIFIED: Dynamic Database Configuration for Render + Neon ---
database_url = os.environ.get('DATABASE_URL')
if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = database_url or 'sqlite:///soccer.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Reads from environment variables if set, otherwise falls back to a dev default.
# Set real values with: export SECRET_KEY="..." and export ADMIN_PASSWORD="..."
app.secret_key = os.environ.get('SECRET_KEY', 'super_secret_pitch_key_change_me')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin123')

db = SQLAlchemy(app)

# --- DATABASE MODEL ---
class Player(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    is_gk = db.Column(db.Boolean, default=False)
    skill = db.Column(db.Integer, default=5)

with app.app_context():
    db.create_all()

# --- ADDED: Keep-Alive Route for UptimeRobot ---
@app.route('/keep-alive')
def keep_alive():
    return "I am awake", 200

# --- BALANCING ALGORITHM (Now returns packaged Team dicts) ---
def generate_fair_teams(selected_players, num_teams):
    teams = [[] for _ in range(num_teams)]
    gks = [p for p in selected_players if p.is_gk]
    outfielders = [p for p in selected_players if not p.is_gk]

    random.shuffle(gks)
    for i, gk in enumerate(gks):
        if i < num_teams: teams[i].append(gk)

    random.shuffle(outfielders)
    outfielders.sort(key=lambda x: x.skill, reverse=True)

    max_cap = (len(selected_players) // num_teams) + (1 if len(selected_players) % num_teams != 0 else 0)

    for player in outfielders:
        eligible_teams = [i for i in range(num_teams) if len(teams[i]) < max_cap]
        if not eligible_teams: eligible_teams = list(range(num_teams))

        target_idx = min(eligible_teams, key=lambda i: sum(p.skill for p in teams[i] if not p.is_gk))
        teams[target_idx].append(player)

    # Pack each team into a dictionary containing its players AND its calculated sum.
    # Within each squad: goalkeepers first, then outfielders alphabetically.
    squad_packages = []
    for t in teams:
        squad_packages.append({
            "players": sorted(t, key=lambda p: (not p.is_gk, p.name.lower())),
            "total_skill": sum(p.skill for p in t if not p.is_gk)
        })

    return squad_packages

def build_squad_packages_from_ids(team_id_lists):
    """Given a list of lists of player IDs (one list per team), fetch the
    Player rows and rebuild the same squad package shape used by
    generate_fair_teams, preserving team order and recalculating totals."""
    all_ids = [pid for team in team_id_lists for pid in team]
    players_by_id = {p.id: p for p in Player.query.filter(Player.id.in_(all_ids)).all()}

    squad_packages = []
    for team_ids in team_id_lists:
        team_players = [players_by_id[pid] for pid in team_ids if pid in players_by_id]
        squad_packages.append({
            "players": sorted(team_players, key=lambda p: (not p.is_gk, p.name.lower())),
            "total_skill": sum(p.skill for p in team_players if not p.is_gk)
        })
    return squad_packages

def skill_color(skill):
    """Map a skill rating (1-10) to a soft, muted red -> yellow -> green
    tint, used for the admin skill-rating dropdown. Colors are blended
    toward the paper background so they read as a gentle hint rather
    than a bold fill — 1 leans pale red, 5-6 pale yellow, 10 pale green."""
    skill = max(1, min(10, skill))
    t = (skill - 1) / 9.0  # normalize to 0..1

    stops = [
        (0.0, (220, 38, 38)),   # red
        (0.5, (234, 179, 8)),   # yellow
        (1.0, (22, 163, 74)),   # green
    ]
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if t0 <= t <= t1:
            local_t = (t - t0) / (t1 - t0) if t1 > t0 else 0
            r = c0[0] + (c1[0] - c0[0]) * local_t
            g = c0[1] + (c1[1] - c0[1]) * local_t
            b = c0[2] + (c1[2] - c0[2]) * local_t

            # Blend toward the paper tone (#F1EFE6) so the result is a
            # soft tint instead of a saturated fill.
            paper = (241, 239, 230)
            blend = 0.72  # 0 = full color, 1 = full paper
            r = round(r + (paper[0] - r) * blend)
            g = round(g + (paper[1] - g) * blend)
            b = round(b + (paper[2] - b) * blend)
            return f'#{r:02x}{g:02x}{b:02x}'
    return '#dcebe0'

def skill_text_color(skill):
    """A readable, muted text color to pair with skill_color's pale tint —
    a deepened version of the same hue rather than plain black, so the
    number still carries the red/yellow/green signal at a glance."""
    skill = max(1, min(10, skill))
    t = (skill - 1) / 9.0

    stops = [
        (0.0, (153, 27, 27)),    # deep red
        (0.5, (133, 77, 14)),    # deep amber
        (1.0, (22, 101, 52)),    # deep green
    ]
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if t0 <= t <= t1:
            local_t = (t - t0) / (t1 - t0) if t1 > t0 else 0
            r = round(c0[0] + (c1[0] - c0[0]) * local_t)
            g = round(c0[1] + (c1[1] - c0[1]) * local_t)
            b = round(c0[2] + (c1[2] - c0[2]) * local_t)
            return f'#{r:02x}{g:02x}{b:02x}'
    return '#166534'

app.jinja_env.globals['skill_color'] = skill_color
app.jinja_env.globals['skill_text_color'] = skill_text_color

# --- MASTER HTML & UI TEMPLATE ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Matchday Sheet</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=Teko:wght@500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --pitch-dark: #1B4332;
            --pitch: #2D6A4F;
            --paper: #F1EFE6;
            --paper-dim: #E7E3D4;
            --ink: #1A1A1A;
            --subink: #5C6B62;
            --amber: #D4A017;
            --amber-deep: #A87908;
            --line: rgba(26,26,26,0.12);
            --red-card: #B3261E;
        }

        * { box-sizing: border-box; }

        body {
            font-family: 'Inter', system-ui, sans-serif;
            background: var(--pitch-dark);
            background-image:
                repeating-linear-gradient(0deg, rgba(255,255,255,0.025) 0px, rgba(255,255,255,0.025) 1px, transparent 1px, transparent 38px),
                radial-gradient(circle at 50% 0%, var(--pitch) 0%, var(--pitch-dark) 70%);
            color: var(--ink);
            margin: 0; padding: 20px 16px 60px;
            min-height: 100vh;
            -webkit-tap-highlight-color: transparent;
        }

        .container { max-width: 480px; margin: 0 auto; }
        .container-wide { max-width: 940px; margin: 0 auto; }

        /* --- HEADER: stitched matchday ticket --- */
        .app-header {
            display: flex; justify-content: space-between; align-items: flex-end;
            margin-bottom: 18px; padding: 0 4px 14px;
            border-bottom: 2px dashed rgba(241,239,230,0.35);
        }
        .app-header .eyebrow {
            font-family: 'Teko', sans-serif; font-size: 0.95rem; font-weight: 600;
            letter-spacing: 3px; color: var(--amber); text-transform: uppercase; margin: 0 0 2px;
        }
        .app-header h1 {
            font-family: 'Teko', sans-serif; font-size: 2.4rem; font-weight: 700;
            color: var(--paper); margin: 0; letter-spacing: 0.5px; line-height: 1;
        }
        .app-header p { margin: 4px 0 0; font-size: 0.8rem; color: rgba(241,239,230,0.65); font-weight: 500; }

        .pill-link {
            font-size: 0.78rem; font-weight: 700; color: var(--pitch-dark); text-decoration: none;
            background: var(--paper); padding: 7px 14px; border-radius: 4px;
            border: 1px solid rgba(0,0,0,0.1); box-shadow: 0 2px 0 rgba(0,0,0,0.15);
        }

        /* --- CLIPBOARD / TEAM SHEET CARD --- */
        .clipboard {
            background: var(--paper);
            border-radius: 8px;
            position: relative;
            box-shadow: 0 14px 30px rgba(0,0,0,0.25);
            padding: 18px 16px 16px;
        }
        .clipboard::before {
            content: '';
            position: absolute; top: -9px; left: 50%; transform: translateX(-50%);
            width: 64px; height: 18px; background: #8a8478;
            border-radius: 5px; box-shadow: inset 0 2px 3px rgba(0,0,0,0.3);
        }

        .section-label {
            font-family: 'Teko', sans-serif; font-size: 0.85rem; font-weight: 600;
            letter-spacing: 2px; color: var(--subink); text-transform: uppercase;
            margin: 0 0 8px; display: block;
        }

        select.ui-input, input.ui-input {
            width: 100%; padding: 12px 14px; border: 1.5px solid var(--line); border-radius: 6px;
            margin-bottom: 14px; font-size: 0.95rem; font-family: 'Inter', sans-serif; font-weight: 600;
            box-sizing: border-box; outline: none; background: #ffffff; color: var(--ink);
        }
        select.ui-input:focus, input.ui-input:focus { border-color: var(--pitch); }

        .top-row { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 6px; }
        #count-display {
            font-family: 'Teko', sans-serif; font-size: 0.95rem; font-weight: 600;
            color: var(--pitch); letter-spacing: 0.5px;
        }

        /* --- Roster rows: team-sheet line items --- */
        .list-trap {
            max-height: 360px; overflow-y: auto;
            border: 1.5px solid var(--line); border-radius: 6px;
            background: #ffffff; margin-bottom: 14px;
        }

        .row {
            display: flex; justify-content: space-between; align-items: center;
            padding: 13px 14px; border-bottom: 1px solid var(--paper-dim);
            cursor: pointer; user-select: none; font-size: 0.96rem; font-weight: 600;
            position: relative;
        }
        .row:last-child { border-bottom: none; }
        .row:hover { background: #faf9f4; }
        .row.checked { background: #EAF3EC; }
        .row.checked .p-name::after { content: ''; }

        .check-mark {
            width: 21px; height: 21px; border-radius: 50%;
            border: 2px solid var(--line); flex-shrink: 0;
            display: flex; align-items: center; justify-content: center;
            transition: all 0.12s;
        }
        .row.checked .check-mark {
            background: var(--pitch); border-color: var(--pitch);
        }
        .check-mark svg { width: 12px; height: 12px; opacity: 0; transition: opacity 0.1s; }
        .row.checked .check-mark svg { opacity: 1; }

        .row-left { display: flex; align-items: center; gap: 12px; }

        /* --- Badges (skill = admin only / GK always visible) --- */
        .sk-badge {
            padding: 3px 9px; border-radius: 4px; font-size: 0.72rem; font-weight: 800;
            min-width: 30px; text-align: center; font-family: 'Teko', sans-serif; letter-spacing: 0.5px;
        }
        .sk-gk { background: var(--ink); color: #ffffff; font-size: 0.68rem; }

        /* --- Buttons --- */
        .btn {
            display: block; width: 100%; padding: 15px; border-radius: 6px;
            font-size: 1rem; font-weight: 700; cursor: pointer; border: none;
            text-align: center; text-decoration: none; box-sizing: border-box;
            font-family: 'Teko', sans-serif; letter-spacing: 1px; text-transform: uppercase;
            transition: transform 0.08s, opacity 0.15s;
        }
        .btn-primary {
            background: var(--amber); color: var(--ink);
            box-shadow: 0 4px 0 var(--amber-deep);
            margin-top: 4px; font-size: 1.15rem;
        }
        .btn-primary:active { transform: translateY(3px); box-shadow: 0 1px 0 var(--amber-deep); }
        .btn-primary:disabled {
            background: #d4cfc0; color: rgba(26,26,26,0.4); box-shadow: 0 4px 0 #b8b2a0;
            cursor: not-allowed;
        }
        .btn-primary:disabled:active { transform: none; box-shadow: 0 4px 0 #b8b2a0; }

        .count-warning {
            display: none; font-size: 0.82rem; font-weight: 700; color: var(--red-card);
            background: #f4d7d4; border: 1.5px solid #e8b6b1; border-radius: 6px;
            padding: 10px 12px; margin-bottom: 10px; text-align: center;
        }
        .count-warning.show { display: block; }
        .btn-dark { background: var(--pitch-dark); color: var(--paper); box-shadow: 0 4px 0 #0d2b1c; }
        .btn-dark:active { transform: translateY(3px); box-shadow: 0 1px 0 #0d2b1c; }
        .btn-ghost {
            background: transparent; color: rgba(241,239,230,0.7); font-size: 0.85rem;
            font-family: 'Inter', sans-serif; font-weight: 600; text-transform: none; letter-spacing: 0;
            border: 1px solid rgba(241,239,230,0.25);
        }

        .btn-row { display: flex; gap: 8px; margin-bottom: 12px; }
        .btn-mini {
            flex: 1; padding: 9px; font-size: 0.78rem; font-weight: 700; background: #ffffff;
            border: 1.5px solid var(--line); border-radius: 5px; color: var(--subink); cursor: pointer;
            font-family: 'Inter', sans-serif;
        }

        /* --- RESULTS: squads laid out like a lineup card --- */
        .squad-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
            gap: 16px; margin-bottom: 22px;
        }

        .squad-card {
            background: var(--paper); border-radius: 8px;
            box-shadow: 0 10px 22px rgba(0,0,0,0.2); overflow: hidden;
            display: flex; flex-direction: column; justify-content: space-between;
            border-top: 6px solid var(--team-color, var(--pitch-dark));
        }

        .squad-card-header {
            background: var(--pitch-dark); padding: 12px 16px;
            display: flex; justify-content: space-between; align-items: center;
            gap: 10px;
        }
        .squad-card-header h3 {
            margin: 0; font-family: 'Teko', sans-serif; font-size: 1.3rem; font-weight: 700;
            color: var(--paper); letter-spacing: 0.5px;
            display: flex; align-items: center; gap: 8px;
        }

        .jersey-dot {
            width: 14px; height: 14px; border-radius: 50%; flex-shrink: 0;
            border: 2px solid rgba(255,255,255,0.5);
            box-shadow: inset 0 1px 2px rgba(0,0,0,0.25);
        }

        .jersey-select {
            font-family: 'Inter', sans-serif; font-size: 0.68rem; font-weight: 700;
            padding: 3px 6px; border-radius: 4px; border: 1.5px solid rgba(255,255,255,0.25);
            background: rgba(255,255,255,0.08); color: var(--paper); cursor: pointer;
        }
        .jersey-select option { color: #1A1A1A; }

        .stat-tag {
            font-size: 0.7rem; font-weight: 800; color: var(--pitch-dark);
            background: var(--amber); padding: 4px 9px; border-radius: 4px;
            font-family: 'Teko', sans-serif; letter-spacing: 0.5px;
        }

        .squad-footer {
            background: #ffffff; padding: 8px 16px; font-size: 0.72rem; color: var(--subink);
            text-align: right; border-top: 1px solid var(--paper-dim); font-weight: 600;
            letter-spacing: 0.5px; text-transform: uppercase;
        }

        /* --- Admin login --- */
        .login-card {
            background: var(--paper); border-radius: 8px; padding: 36px 26px; text-align: center;
            margin-top: 40px; box-shadow: 0 14px 30px rgba(0,0,0,0.25); position: relative;
        }
        .login-card::before {
            content: ''; position: absolute; top: -9px; left: 50%; transform: translateX(-50%);
            width: 64px; height: 18px; background: #8a8478; border-radius: 5px;
            box-shadow: inset 0 2px 3px rgba(0,0,0,0.3);
        }
        .login-card h2 {
            font-family: 'Teko', sans-serif; font-size: 1.8rem; margin: 0 0 4px; color: var(--ink);
        }
        .login-card p { font-size: 0.85rem; color: var(--subink); margin-bottom: 22px; }
        .error-msg {
            color: var(--red-card); font-size: 0.85rem; font-weight: 700; margin-bottom: 16px;
        }
        .back-link {
            display: block; margin-top: 18px; font-size: 0.85rem; color: rgba(241,239,230,0.6);
            text-decoration: none; text-align: center;
        }

        /* --- Admin add-player panel --- */
        .admin-panel {
            background: var(--paper); border-radius: 8px; padding: 16px; margin-bottom: 18px;
        }
        .admin-panel-title {
            font-family: 'Teko', sans-serif; font-weight: 700; font-size: 1.05rem;
            letter-spacing: 1px; margin-bottom: 10px; color: var(--pitch); text-transform: uppercase;
        }
        .field-grid { display: flex; gap: 10px; }
        .field-grid > div { flex: 1; }
        .field-label {
            font-size: 0.68rem; font-weight: 700; color: var(--subink); letter-spacing: 0.5px;
            text-transform: uppercase; display: block; margin-bottom: 4px;
        }

        .delete-btn {
            background: #f4d7d4; color: var(--red-card); border: none; border-radius: 5px;
            padding: 5px 9px; font-weight: bold; cursor: pointer; font-size: 0.85rem;
        }

        /* --- Admin: inline editable rating/position --- */
        .edit-form {
            display: flex; align-items: center; gap: 8px;
        }
        .mini-select {
            font-family: 'Teko', sans-serif; font-weight: 700; font-size: 0.85rem;
            padding: 4px 6px; border-radius: 4px; border: 1.5px solid var(--line);
            background: #ffffff; color: var(--ink); cursor: pointer;
        }
        .mini-select.skill-select { width: 56px; text-align: center; }

        /* --- Results: drag-and-drop + swap controls --- */
        .squad-list { min-height: 12px; }
        .squad-player {
            padding: 10px 16px; border-bottom: 1px solid var(--paper-dim);
            display: flex; justify-content: space-between; align-items: center;
            font-size: 0.95rem; font-weight: 600;
            touch-action: none; position: relative;
        }
        .squad-player:last-child { border-bottom: none; }
        .squad-player.dragging { opacity: 0.35; }
        .squad-list.drag-over { background: #EAF3EC; }

        .player-left { display: flex; align-items: center; gap: 8px; min-width: 0; }
        .drag-handle {
            cursor: grab; color: var(--subink); opacity: 0.45; flex-shrink: 0;
            width: 16px; height: 16px; touch-action: none;
        }
        .drag-handle:active { cursor: grabbing; }
        .player-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

        .drag-ghost {
            position: fixed; pointer-events: none; z-index: 999;
            background: var(--paper); border: 2px solid var(--pitch);
            border-radius: 6px; padding: 8px 14px; font-size: 0.9rem; font-weight: 700;
            box-shadow: 0 8px 20px rgba(0,0,0,0.3); opacity: 0.95;
        }

        .swap-select {
            font-family: 'Inter', sans-serif; font-size: 0.68rem; font-weight: 600;
            padding: 3px 4px; border-radius: 4px; border: 1.5px solid var(--line);
            background: #ffffff; color: var(--subink); cursor: pointer; max-width: 88px;
            flex-shrink: 0;
        }
        .swap-select:focus { border-color: var(--pitch); }
        .swap-hint {
            font-size: 0.72rem; color: rgba(241,239,230,0.6); text-align: center;
            margin: -6px 0 16px; font-weight: 500;
        }

        /* On phones, stack teams one after another (not side-by-side) and
            tighten vertical spacing, so a full set of squads fits in a
            single screenshot for the group chat. Placed last so it wins
            the cascade over the base rules above. */
        @media (max-width: 640px) {
            .squad-grid { grid-template-columns: 1fr; gap: 10px; }
            .squad-card-header { padding: 9px 14px; }
            .squad-card-header h3 { font-size: 1.15rem; }
            .squad-player { padding: 7px 14px; font-size: 0.88rem; }
            .squad-footer { padding: 5px 14px; }
            .app-header { margin-bottom: 12px; padding-bottom: 10px; }
            .app-header h1 { font-size: 1.9rem; }
            .drag-handle { width: 18px; height: 18px; opacity: 0.55; }
        }
    </style>
</head>
<body>
    <div class="{% if view == 'results' %}container-wide{% else %}container{% endif %}">

        {% if view == 'main' %}
        <div class="app-header">
            <div>
                <span class="eyebrow">Sunday League</span>
                <h1>Matchday Sheet</h1>
                <p>Check in who's showing up</p>
            </div>
            <a href="/admin" class="pill-link">Admin</a>
        </div>

        {% if error %}<div class="count-warning show" style="margin: 0 0 14px;">{{ error }}</div>{% endif %}

        <div class="clipboard">
            <form action="/shuffle" method="POST" id="shuffle-form">
                <span class="section-label">Match Format</span>
                <select name="num_teams" id="num-teams-select" class="ui-input" onchange="updateCount()">
                    <option value="3">3 Squads — 5-a-side rotation (needs 15)</option>
                    <option value="2">2 Squads — Standard game (needs 10)</option>
                </select>

                <div class="top-row">
                    <span class="section-label" style="margin:0;">Roster ({{ players|length }})</span>
                    <span id="count-display">0 checked in</span>
                </div>

                <input type="text" class="ui-input" placeholder="Search player..." onkeyup="filterUI(this, 'roster-box')">

                <div class="btn-row">
                    <button type="button" class="btn-mini" onclick="checkAll(true)">All In</button>
                    <button type="button" class="btn-mini" onclick="checkAll(false)">Clear</button>
                </div>

                <div class="list-trap" id="roster-box">
                    {% for p in players %}
                    <div class="row" onclick="toggleRow(this)">
                        <div class="row-left">
                            <div class="check-mark">
                                <svg viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>
                            </div>
                            <input type="checkbox" name="selected_players" value="{{ p.id }}" onchange="updateCount()" style="display:none;">
                            <span class="p-name">{{ p.name }}</span>
                        </div>
                        {% if p.is_gk %}<span class="sk-badge sk-gk">GK</span>{% endif %}
                    </div>
                    {% endfor %}
                </div>

                <div id="count-warning" class="count-warning"></div>

                <button type="submit" class="btn btn-primary" id="pick-teams-btn">Pick Teams</button>
            </form>
        </div>


        {% elif view == 'results' %}
        <div class="app-header">
            <div>
                <span class="eyebrow">Kickoff Ready</span>
                <h1>Squads Are Set</h1>
                <p>Balanced by skill, split by position</p>
            </div>
            <span class="pill-link" style="background:var(--paper); color:var(--pitch-dark);">{{ total_players }} players</span>
        </div>

        <div class="squad-grid" id="squad-grid">
            {% for squad in teams %}
            {% set this_team_idx = loop.index0 %}
            {% set jersey = jersey_colors[this_team_idx] if this_team_idx < jersey_colors|length else None %}
            <div class="squad-card" id="card-{{ this_team_idx }}" style="--team-color: {{ jersey.hex if jersey else 'var(--pitch-dark)' }};">
                <div>
                    <div class="squad-card-header">
                        <h3>
                            <span class="jersey-dot" id="dot-{{ this_team_idx }}" style="background:{{ jersey.hex if jersey else 'transparent' }};"></span>
                            Team {{ loop.index }}
                        </h3>
                        <div style="display:flex; align-items:center; gap:8px;">
                            <select class="jersey-select" id="jersey-select-{{ this_team_idx }}" onchange="changeJersey({{ this_team_idx }}, this.value)">
                                {% for jc in jersey_choices %}
                                <option value="{{ jc.key }}" {% if jersey and jersey.key == jc.key %}selected{% endif %}>{{ jc.name }}</option>
                                {% endfor %}
                            </select>
                            <span class="stat-tag" id="rating-{{ this_team_idx }}">Rating {{ squad.total_skill }}</span>
                        </div>
                    </div>
                    <div class="squad-list" data-team-idx="{{ this_team_idx }}">
                        {% for p in squad.players %}
                        <div class="squad-player" draggable="true" data-player-id="{{ p.id }}" data-skill="{{ p.skill }}" data-gk="{{ 'true' if p.is_gk else 'false' }}">
                            <div class="player-left">
                                <svg class="drag-handle" viewBox="0 0 24 24" fill="currentColor"><circle cx="9" cy="6" r="1.6"/><circle cx="15" cy="6" r="1.6"/><circle cx="9" cy="12" r="1.6"/><circle cx="15" cy="12" r="1.6"/><circle cx="9" cy="18" r="1.6"/><circle cx="15" cy="18" r="1.6"/></svg>
                                <span class="player-name">{{ p.name }}{% if p.is_gk %} <span class="sk-badge sk-gk" style="font-size:0.6rem; vertical-align:1px;">GK</span>{% endif %}</span>
                            </div>
                            <select class="swap-select" onchange="moveViaDropdown('{{ p.id }}', this.value); this.value='';">
                                <option value="">Move...</option>
                                {% for target_idx in range(teams|length) %}
                                    {% if target_idx != this_team_idx %}
                                    <option value="{{ target_idx }}">Team {{ target_idx + 1 }}</option>
                                    {% endif %}
                                {% endfor %}
                            </select>
                        </div>
                        {% endfor %}
                    </div>
                </div>
                <div class="squad-footer" id="count-{{ this_team_idx }}">{{ squad.players|length }} players</div>
            </div>
            {% endfor %}
        </div>

        <p class="swap-hint">Drag a player onto another squad, or use "Move..." to send them there</p>

        <div style="max-width:400px; margin: 0 auto; display:flex; flex-direction:column; gap:10px;">
            <form action="/shuffle" method="POST" style="margin:0;">
                <input type="hidden" name="num_teams" value="{{ num_teams }}">
                {% for pid in selected_ids %}<input type="hidden" name="selected_players" value="{{ pid }}">{% endfor %}
                <button type="submit" class="btn btn-dark">Reshuffle Same Players</button>
            </form>
            <a href="/" class="btn btn-ghost">← Edit check-in list</a>
        </div>


        {% elif view == 'login' %}
        <div class="login-card">
            <h2>Admin Passcode</h2>
            <p>Unlock roster &amp; skill ratings</p>

            {% if error %}<div class="error-msg">{{ error }}</div>{% endif %}

            <form action="/admin" method="POST">
                <input type="password" name="password" class="ui-input" placeholder="••••••••" style="text-align:center;" required autofocus>
                <button type="submit" class="btn btn-primary">Authorize</button>
            </form>
            <a href="/" class="back-link">← Back to matchday sheet</a>
        </div>


        {% elif view == 'admin' %}
        <div class="app-header">
            <div>
                <span class="eyebrow">Backroom</span>
                <h1>Database Control</h1>
                <p>Manage the player pool &amp; ratings</p>
            </div>
            <div style="display:flex; gap:8px;">
                <a href="/admin/logout" class="pill-link" style="color:var(--red-card); background:#f4d7d4;">Lock</a>
                <a href="/" class="pill-link" style="background:var(--pitch-dark); color:var(--paper);">Sheet</a>
            </div>
        </div>

        <div class="admin-panel">
            <div class="admin-panel-title">+ Register New Player</div>
            <form action="/admin/add" method="POST" style="margin:0;">
                <input type="text" name="name" class="ui-input" placeholder="Player name..." required autocomplete="off">
                <div class="field-grid">
                    <div>
                        <span class="field-label">Position</span>
                        <select name="is_gk" class="ui-input">
                            <option value="false">Outfield</option>
                            <option value="true">Goalkeeper</option>
                        </select>
                    </div>
                    <div>
                        <span class="field-label">Skill Rating</span>
                        <select name="skill" class="ui-input" id="new-skill-select" onchange="this.style.backgroundColor=this.options[this.selectedIndex].dataset.color; this.style.color=this.options[this.selectedIndex].dataset.textcolor;" style="background-color:{{ skill_color(5) }}; color:{{ skill_text_color(5) }};">
                            <option value="10" data-color="{{ skill_color(10) }}" data-textcolor="{{ skill_text_color(10) }}" style="background-color:{{ skill_color(10) }}; color:{{ skill_text_color(10) }};">10 — Pro</option>
                            <option value="9" data-color="{{ skill_color(9) }}" data-textcolor="{{ skill_text_color(9) }}" style="background-color:{{ skill_color(9) }}; color:{{ skill_text_color(9) }};">9</option>
                            <option value="8" data-color="{{ skill_color(8) }}" data-textcolor="{{ skill_text_color(8) }}" style="background-color:{{ skill_color(8) }}; color:{{ skill_text_color(8) }};">8</option>
                            <option value="7" data-color="{{ skill_color(7) }}" data-textcolor="{{ skill_text_color(7) }}" style="background-color:{{ skill_color(7) }}; color:{{ skill_text_color(7) }};">7</option>
                            <option value="6" data-color="{{ skill_color(6) }}" data-textcolor="{{ skill_text_color(6) }}" style="background-color:{{ skill_color(6) }}; color:{{ skill_text_color(6) }};">6</option>
                            <option value="5" data-color="{{ skill_color(5) }}" data-textcolor="{{ skill_text_color(5) }}" style="background-color:{{ skill_color(5) }}; color:{{ skill_text_color(5) }};" selected>5 — Decent</option>
                            <option value="4" data-color="{{ skill_color(4) }}" data-textcolor="{{ skill_text_color(4) }}" style="background-color:{{ skill_color(4) }}; color:{{ skill_text_color(4) }};">4</option>
                            <option value="3" data-color="{{ skill_color(3) }}" data-textcolor="{{ skill_text_color(3) }}" style="background-color:{{ skill_color(3) }}; color:{{ skill_text_color(3) }};">3</option>
                            <option value="2" data-color="{{ skill_color(2) }}" data-textcolor="{{ skill_text_color(2) }}" style="background-color:{{ skill_color(2) }}; color:{{ skill_text_color(2) }};">2</option>
                            <option value="1" data-color="{{ skill_color(1) }}" data-textcolor="{{ skill_text_color(1) }}" style="background-color:{{ skill_color(1) }}; color:{{ skill_text_color(1) }};">1 — Average</option>
                        </select>
                    </div>
                </div>
                <button type="submit" class="btn btn-primary" style="padding:12px; font-size:1rem;">Save to Database</button>
            </form>
        </div>

        <input type="text" class="ui-input" placeholder="Search database..." onkeyup="filterUI(this, 'admin-roster')">

        <div class="list-trap" id="admin-roster">
            {% for p in players %}
            <div class="row" style="cursor:default;">
                <span class="p-name" style="font-weight:700;">{{ p.name }}</span>
                <div style="display:flex; align-items:center; gap:12px;">
                    <form action="/admin/update/{{ p.id }}" method="POST" class="edit-form">
                        <select name="is_gk" class="mini-select" onchange="this.form.submit()">
                            <option value="false" {% if not p.is_gk %}selected{% endif %}>OUT</option>
                            <option value="true" {% if p.is_gk %}selected{% endif %}>GK</option>
                        </select>
                        <select name="skill" class="mini-select skill-select"
                                style="background-color:{{ skill_color(p.skill) }}; color:{{ skill_text_color(p.skill) }}; border-color:{{ skill_color(p.skill) }}; font-weight:800;"
                                data-textcolor="{{ skill_text_color(p.skill) }}"
                                onchange="this.style.backgroundColor=this.options[this.selectedIndex].dataset.color; this.style.borderColor=this.options[this.selectedIndex].dataset.color; this.style.color=this.options[this.selectedIndex].dataset.textcolor; this.form.submit();"
                                {% if p.is_gk %}disabled{% endif %}>
                            {% for s in range(1, 11) %}
                            <option value="{{ s }}" data-color="{{ skill_color(s) }}" data-textcolor="{{ skill_text_color(s) }}" style="background-color:{{ skill_color(s) }}; color:{{ skill_text_color(s) }};" {% if p.skill == s %}selected{% endif %}>{{ s }}</option>
                            {% endfor %}
                        </select>
                    </form>

                    <form action="/admin/delete/{{ p.id }}" method="POST" style="margin:0;">
                        <button type="submit" class="delete-btn" onclick="return confirm('Delete {{ p.name }}?')">✕</button>
                    </form>
                </div>
            </div>
            {% endfor %}
        </div>
        {% endif %}

    </div>

    <script>
        // Set the initial locked/unlocked state on page load (matters on
        // the main check-in page, where 0 players are selected at first).
        document.addEventListener('DOMContentLoaded', updateCount);

        function toggleRow(el) {
            const cb = el.querySelector('input[type="checkbox"]');
            cb.checked = !cb.checked;
            el.classList.toggle('checked', cb.checked);
            updateCount();
        }
        function checkAll(state) {
            document.querySelectorAll('#roster-box .row').forEach(row => {
                const cb = row.querySelector('input[type="checkbox"]');
                cb.checked = state;
                state ? row.classList.add('checked') : row.classList.remove('checked');
            });
            updateCount();
        }
        function updateCount() {
            const display = document.getElementById('count-display');
            const warning = document.getElementById('count-warning');
            const btn = document.getElementById('pick-teams-btn');
            const formatSelect = document.getElementById('num-teams-select');
            if (!display || !btn || !formatSelect) return;

            const count = document.querySelectorAll('#roster-box input[type="checkbox"]:checked').length;
            display.innerText = count + " checked in";

            // 2 squads need exactly 10 players (5v5), 3 squads need exactly
            // 15 (5v5v5 rotation). Lock "Pick Teams" until the count matches.
            const required = formatSelect.value === '2' ? 10 : 15;

            if (count === required) {
                btn.disabled = false;
                warning.classList.remove('show');
                warning.innerText = '';
            } else {
                btn.disabled = true;
                const diff = required - count;
                warning.classList.add('show');
                if (diff > 0) {
                    warning.innerText = `Need ${diff} more player${diff === 1 ? '' : 's'} (${required} required for this format)`;
                } else {
                    warning.innerText = `${-diff} too many (${required} required for this format)`;
                }
            }
        }
        function filterUI(inputEl, boxId) {
            const query = inputEl.value.toLowerCase();
            const rows = document.getElementById(boxId).getElementsByClassName('row');
            for (let row of rows) {
                const name = row.getElementsByClassName('p-name')[0].innerText.toLowerCase();
                row.style.display = name.includes(query) ? "flex" : "none";
            }
        }

        // --- Jersey color selection (results page) ---
        const JERSEY_HEX = {{ (jersey_hex_map if jersey_hex_map is defined else {})|tojson }};

        function changeJersey(teamIdx, colorKey) {
            const card = document.getElementById('card-' + teamIdx);
            const dot = document.getElementById('dot-' + teamIdx);
            const hex = JERSEY_HEX[colorKey] || 'transparent';
            if (card) card.style.setProperty('--team-color', hex);
            if (dot) dot.style.background = hex;

            const fd = new FormData();
            fd.append('team_idx', teamIdx);
            fd.append('color_key', colorKey);
            fetch('/jersey', { method: 'POST', body: fd }).catch(() => {});
        }

        // --- Drag-and-drop team swapping (results page) ---
        // Uses pointer events (not native HTML5 DnD) so this works the
        // same way with mouse, touch, and pen input.
        (function initDragAndDrop() {
            const grid = document.getElementById('squad-grid');
            if (!grid) return;

            let dragEl = null;
            let ghost = null;
            let startX = 0, startY = 0;
            let dragging = false;

            function persistSwap(playerId, targetIdx) {
                const fd = new FormData();
                fd.append('player_id', playerId);
                fd.append('target_team', targetIdx);
                fetch('/swap', { method: 'POST', body: fd }).catch(() => {
                    // Network hiccup: state already moved optimistically in the
                    // DOM. A reshuffle or refresh will re-sync from the server,
                    // so we don't need to roll back the UI here.
                });
            }

            function recalcTeam(teamIdx) {
                const list = grid.querySelector('.squad-list[data-team-idx="' + teamIdx + '"]');
                if (!list) return;
                const players = list.querySelectorAll('.squad-player');
                let total = 0;
                players.forEach(p => {
                    if (p.dataset.gk !== 'true') total += parseInt(p.dataset.skill || '0', 10);
                });
                const ratingEl = document.getElementById('rating-' + teamIdx);
                const countEl = document.getElementById('count-' + teamIdx);
                if (ratingEl) ratingEl.innerText = 'Rating ' + total;
                if (countEl) countEl.innerText = players.length + (players.length === 1 ? ' player' : ' players');
            }

            function movePlayerTo(playerEl, targetList) {
                const fromList = playerEl.closest('.squad-list');
                if (!fromList || fromList === targetList) return;
                const fromIdx = fromList.dataset.teamIdx;
                const toIdx = targetList.dataset.teamIdx;
                targetList.appendChild(playerEl);
                recalcTeam(fromIdx);
                recalcTeam(toIdx);
                persistSwap(playerEl.dataset.playerId, toIdx);
            }

            function clearDragOver() {
                grid.querySelectorAll('.squad-list.drag-over').forEach(el => el.classList.remove('drag-over'));
            }

            function onPointerDown(e) {
                // Start the drag from anywhere on the row (name, handle,
                // whitespace) — just not from the "Move..." dropdown,
                // which needs normal click/tap behavior to open.
                if (e.target.closest('.swap-select')) return;
                const playerEl = e.target.closest('.squad-player');
                if (!playerEl) return;
                e.preventDefault();
                dragEl = playerEl;
                startX = e.clientX; startY = e.clientY;
                dragging = false;

                document.addEventListener('pointermove', onPointerMove);
                document.addEventListener('pointerup', onPointerUp, { once: true });
            }

            function onPointerMove(e) {
                if (!dragEl) return;
                const dx = e.clientX - startX, dy = e.clientY - startY;
                if (!dragging && Math.hypot(dx, dy) < 6) return;

                if (!dragging) {
                    dragging = true;
                    dragEl.classList.add('dragging');
                    ghost = document.createElement('div');
                    ghost.className = 'drag-ghost';
                    ghost.innerText = dragEl.querySelector('.player-name').innerText.trim();
                    document.body.appendChild(ghost);
                }

                ghost.style.left = (e.clientX + 12) + 'px';
                ghost.style.top = (e.clientY + 12) + 'px';

                clearDragOver();
                const under = document.elementFromPoint(e.clientX, e.clientY);
                const list = under && under.closest && under.closest('.squad-list');
                if (list) list.classList.add('drag-over');
            }

            function onPointerUp(e) {
                document.removeEventListener('pointermove', onPointerMove);
                if (!dragEl) return;

                if (dragging) {
                    const under = document.elementFromPoint(e.clientX, e.clientY);
                    const list = under && under.closest && under.closest('.squad-list');
                    if (list) movePlayerTo(dragEl, list);
                }

                dragEl.classList.remove('dragging');
                clearDragOver();
                if (ghost) { ghost.remove(); ghost = null; }
                dragEl = null;
                dragging = false;
            }

            grid.addEventListener('pointerdown', onPointerDown);

            // Prevent native HTML5 drag (from the draggable="true" attribute,
            // kept only as a graceful no-op fallback) from fighting the
            // pointer-based drag above.
            grid.addEventListener('dragstart', e => e.preventDefault());
        })();

        // Dropdown fallback: works identically to dragging, just via select.
        function moveViaDropdown(playerId, targetIdx) {
            if (targetIdx === '') return;
            const grid = document.getElementById('squad-grid');
            const playerEl = grid.querySelector('.squad-player[data-player-id="' + playerId + '"]');
            const targetList = grid.querySelector('.squad-list[data-team-idx="' + targetIdx + '"]');
            if (!playerEl || !targetList) return;

            const fromList = playerEl.closest('.squad-list');
            const fromIdx = fromList.dataset.teamIdx;
            targetList.appendChild(playerEl);

            const recalc = (idx) => {
                const list = grid.querySelector('.squad-list[data-team-idx="' + idx + '"]');
                const players = list.querySelectorAll('.squad-player');
                let total = 0;
                players.forEach(p => { if (p.dataset.gk !== 'true') total += parseInt(p.dataset.skill || '0', 10); });
                document.getElementById('rating-' + idx).innerText = 'Rating ' + total;
                document.getElementById('count-' + idx).innerText = players.length + (players.length === 1 ? ' player' : ' players');
            };
            recalc(fromIdx);
            recalc(targetIdx);

            const fd = new FormData();
            fd.append('player_id', playerId);
            fd.append('target_team', targetIdx);
            fetch('/swap', { method: 'POST', body: fd }).catch(() => {});
        }
    </script>
</body>
</html>
"""

# --- ROUTING CONTROLLERS ---

REQUIRED_PLAYERS_BY_FORMAT = {2: 10, 3: 15}

# Jersey color palette: default order is Team 1 = Red, Team 2 = Yellow,
# Team 3 = Blue. Swappable per-team via the dropdown on the results page.
JERSEY_CHOICES = [
    {"key": "red", "name": "Red", "hex": "#C0392B"},
    {"key": "yellow", "name": "Yellow", "hex": "#D4A017"},
    {"key": "blue", "name": "Blue", "hex": "#2C5BA0"},
    {"key": "white", "name": "White", "hex": "#E8E6DA"},
    {"key": "black", "name": "Black", "hex": "#2B2B2B"},
]
JERSEY_HEX_MAP = {jc["key"]: jc["hex"] for jc in JERSEY_CHOICES}
JERSEY_BY_KEY = {jc["key"]: jc for jc in JERSEY_CHOICES}
DEFAULT_JERSEY_ORDER = ["red", "yellow", "blue"]

def default_jersey_keys(num_teams):
    return [DEFAULT_JERSEY_ORDER[i] for i in range(num_teams)]

def get_jersey_keys(num_teams):
    """Read the current per-team jersey color keys from session, falling
    back to the default Red/Yellow/Blue order if not yet customized or
    if the team count changed since the colors were last set."""
    keys = session.get('jersey_keys')
    if not keys or len(keys) != num_teams:
        keys = default_jersey_keys(num_teams)
        session['jersey_keys'] = keys
    return keys

def jersey_objects_for(num_teams):
    keys = get_jersey_keys(num_teams)
    return [JERSEY_BY_KEY.get(k) for k in keys]

@app.route('/')
def index():
    players = Player.query.order_by(Player.name).all()
    return render_template_string(HTML_TEMPLATE, view='main', players=players, error=request.args.get('error'))

@app.route('/shuffle', methods=['POST'])
def shuffle():
    player_ids = request.form.getlist('selected_players')
    num_teams = int(request.form.get('num_teams', 3))
    if not player_ids: return redirect(url_for('index'))

    # Defensive server-side check: the check-in page already locks the
    # button until the count matches, but this guards against a direct
    # POST that skips the UI (devtools, stale page, etc).
    required = REQUIRED_PLAYERS_BY_FORMAT.get(num_teams)
    if required is not None and len(player_ids) != required:
        diff = required - len(player_ids)
        if diff > 0:
            msg = f"Need {diff} more player{'s' if diff != 1 else ''} — {required} required for {num_teams} squads."
        else:
            msg = f"{-diff} too many — {required} required for {num_teams} squads."
        return redirect(url_for('index', error=msg))

    selected_players = Player.query.filter(Player.id.in_(player_ids)).all()
    squad_packages = generate_fair_teams(selected_players, num_teams)

    # Store just the team id-structure in session, so manual swaps and
    # reshuffles can both work from a single source of truth.
    session['team_ids'] = [[p.id for p in squad['players']] for squad in squad_packages]
    session['shuffle_selected_ids'] = player_ids
    session['shuffle_num_teams'] = num_teams
    # Reset jersey colors to the default Red/Yellow/Blue order for a fresh
    # shuffle (a brand new set of teams starts with the standard kit order).
    session['jersey_keys'] = default_jersey_keys(num_teams)

    return render_template_string(
        HTML_TEMPLATE, view='results', teams=squad_packages,
        selected_ids=player_ids, num_teams=num_teams, total_players=len(selected_players),
        jersey_colors=jersey_objects_for(num_teams), jersey_choices=JERSEY_CHOICES,
        jersey_hex_map=JERSEY_HEX_MAP
    )

@app.route('/results')
def results():
    # Lets a page refresh after dragging players around (or recoloring
    # jerseys) re-render the current board, instead of losing it.
    team_ids = session.get('team_ids')
    if not team_ids:
        return redirect(url_for('index'))

    squad_packages = build_squad_packages_from_ids(team_ids)
    total_players = sum(len(s['players']) for s in squad_packages)
    num_teams = session.get('shuffle_num_teams', len(team_ids))

    return render_template_string(
        HTML_TEMPLATE, view='results', teams=squad_packages,
        selected_ids=session.get('shuffle_selected_ids', []),
        num_teams=num_teams,
        total_players=total_players,
        jersey_colors=jersey_objects_for(num_teams), jersey_choices=JERSEY_CHOICES,
        jersey_hex_map=JERSEY_HEX_MAP
    )

@app.route('/swap', methods=['POST'])
def swap():
    team_ids = session.get('team_ids')
    if not team_ids:
        return {'ok': False, 'error': 'no active shuffle'}, 400

    player_id = int(request.form.get('player_id'))
    target_team = request.form.get('target_team')

    if target_team != '':
        target_idx = int(target_team)
        if not (0 <= target_idx < len(team_ids)):
            return {'ok': False, 'error': 'invalid team index'}, 400
        # Remove the player from whichever team currently holds them, then
        # add them to the chosen team. No-op safely if already there.
        for team in team_ids:
            if player_id in team:
                team.remove(player_id)
                break
        team_ids[target_idx].append(player_id)
        session['team_ids'] = team_ids

    return {'ok': True}

@app.route('/jersey', methods=['POST'])
def jersey():
    team_ids = session.get('team_ids')
    if not team_ids:
        return {'ok': False, 'error': 'no active shuffle'}, 400

    try:
        team_idx = int(request.form.get('team_idx'))
    except (TypeError, ValueError):
        return {'ok': False, 'error': 'invalid team index'}, 400

    color_key = request.form.get('color_key', '')
    if color_key not in JERSEY_BY_KEY:
        return {'ok': False, 'error': 'unknown color'}, 400
    if not (0 <= team_idx < len(team_ids)):
        return {'ok': False, 'error': 'invalid team index'}, 400

    num_teams = session.get('shuffle_num_teams', len(team_ids))
    keys = get_jersey_keys(num_teams)
    keys[team_idx] = color_key
    session['jersey_keys'] = keys

    return {'ok': True}

@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if request.method == 'POST':
        if secrets.compare_digest(request.form.get('password', ''), ADMIN_PASSWORD):
            session['admin_unlocked'] = True
            return redirect(url_for('admin'))
        else:
            return render_template_string(HTML_TEMPLATE, view='login', error="Incorrect passcode")

    if not session.get('admin_unlocked'):
        return render_template_string(HTML_TEMPLATE, view='login', error=None)

    players = Player.query.order_by(Player.name).all()
    return render_template_string(HTML_TEMPLATE, view='admin', players=players)

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_unlocked', None)
    return redirect(url_for('index'))

@app.route('/admin/add', methods=['POST'])
def admin_add():
    if not session.get('admin_unlocked'): return redirect(url_for('admin'))
    name = request.form.get('name').strip()
    if name:
        db.session.add(Player(name=name, is_gk=(request.form.get('is_gk') == 'true'), skill=int(request.form.get('skill'))))
        db.session.commit()
    return redirect(url_for('admin'))

@app.route('/admin/delete/<int:player_id>', methods=['POST'])
def admin_delete(player_id):
    if not session.get('admin_unlocked'): return redirect(url_for('admin'))
    p = Player.query.get(player_id)
    if p:
        db.session.delete(p)
        db.session.commit()
    return redirect(url_for('admin'))

@app.route('/admin/update/<int:player_id>', methods=['POST'])
def admin_update(player_id):
    if not session.get('admin_unlocked'): return redirect(url_for('admin'))
    p = Player.query.get(player_id)
    if p:
        p.is_gk = (request.form.get('is_gk') == 'true')
        # Goalkeepers don't carry an outfield skill rating in the balancing
        # algorithm, but we still store whatever was submitted (or keep
        # the existing value) rather than silently zeroing it out.
        skill_raw = request.form.get('skill')
        if skill_raw is not None:
            p.skill = int(skill_raw)
        db.session.commit()
    return redirect(url_for('admin'))

if __name__ == '__main__':
    app.run(debug=True)
