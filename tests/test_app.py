from datetime import datetime

import pytest

from app import User, app, db, get_upcoming_matchweek


@pytest.fixture
def client():
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI='sqlite:///:memory:')
    with app.test_client() as client:
        with app.app_context():
            db.create_all()
        yield client
        with app.app_context():
            db.drop_all()


def test_database_tables_are_created_on_first_request(client):
    with app.app_context():
        db.drop_all()

    response = client.get('/login')
    assert response.status_code == 200
    assert b'Login' in response.data


def test_register_and_login_flow(client):
    response = client.post('/register', data={
        'first_name': 'Jamie',
        'last_initial': 'C',
        'email': 'jamie@example.com',
        'phone': '07777777777',
        'password': 'password123',
        'confirm_password': 'password123',
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b'Welcome back' in response.data or b'Home' in response.data

    login_response = client.post('/login', data={
        'email': 'jamie@example.com',
        'password': 'password123',
    }, follow_redirects=True)
    assert login_response.status_code == 200
    assert b'Home' in login_response.data


def test_register_form_field_names_match_backend(client):
    response = client.post('/register', data={
        'first_name': 'Taylor',
        'last_initial': 'AB',
        'email': 'taylor@example.com',
        'phone': '07111111111',
        'password': 'password123',
        'confirm_password': 'password123',
    }, follow_redirects=True)

    assert response.status_code == 200
    assert b'All fields are required' not in response.data
    assert b'Account created successfully' in response.data or b'Home' in response.data


def test_home_page_shows_fixtures_to_anonymous_users(client):
    response = client.get('/')
    assert response.status_code == 200
    assert b'fixture-scroll' in response.data
    assert b'fixture-badge-tooltip' in response.data


def test_submit_pick_and_view_selection(client):
    client.post('/register', data={
        'first_name': 'Alex',
        'last_initial': 'K',
        'email': 'alex@example.com',
        'phone': '07888888888',
        'password': 'password123',
        'confirm_password': 'password123',
    })
    client.post('/login', data={
        'email': 'alex@example.com',
        'password': 'password123',
    })

    create_response = client.post('/competitions/create', data={
        'name': 'Demo League',
        'code': 'DEMO01',
        'start_matchweek': '1',
    }, follow_redirects=True)
    assert create_response.status_code == 200

    join_response = client.post('/competitions/join', data={'code': 'DEMO01'}, follow_redirects=True)
    assert join_response.status_code == 200

    pick_response = client.post('/submit_pick/1/1', data={'team': 'Arsenal'}, follow_redirects=True)
    assert pick_response.status_code == 200
    assert b'Arsenal' in pick_response.data


def test_future_week_picks_respect_previous_team_usage(client):
    client.post('/register', data={
        'first_name': 'Taylor',
        'last_initial': 'R',
        'email': 'taylor@example.com',
        'phone': '07877777777',
        'password': 'password123',
        'confirm_password': 'password123',
    })
    client.post('/login', data={
        'email': 'taylor@example.com',
        'password': 'password123',
    })

    client.post('/competitions/create', data={
        'name': 'Future Picks',
        'code': 'FUT001',
        'start_matchweek': '1',
    }, follow_redirects=True)
    client.post('/competitions/join', data={'code': 'FUT001'}, follow_redirects=True)

    first_submit = client.post('/submit_pick/1/1', data={'team': 'Arsenal'}, follow_redirects=True)
    assert first_submit.status_code == 200

    second_submit = client.post('/submit_pick/1/2', data={'team': 'Arsenal'}, follow_redirects=True)
    assert b'already been used' in second_submit.data.lower()


def test_submit_pick_page_no_longer_shows_submit_button(client):
    client.post('/register', data={
        'first_name': 'Riley',
        'last_initial': 'M',
        'email': 'riley@example.com',
        'phone': '07000000000',
        'password': 'password123',
        'confirm_password': 'password123',
    })
    client.post('/login', data={
        'email': 'riley@example.com',
        'password': 'password123',
    })

    client.post('/competitions/create', data={
        'name': 'No Submit League',
        'code': 'NOSUB01',
        'start_matchweek': '1',
    }, follow_redirects=True)
    client.post('/competitions/join', data={'code': 'NOSUB01'}, follow_redirects=True)

    response = client.get('/submit_pick/1/1')
    assert response.status_code == 200
    assert b'<button type="submit"' not in response.data


def test_form_table_uses_fixture_teams_and_updates_from_admin_results(client):
    with app.app_context():
        user = User(name='Admin', email='admin@example.com', phone='01111111111', is_admin=True)
        user.set_password('password123')
        db.session.add(user)
        db.session.commit()

    login_response = client.post('/login', data={
        'email': 'admin@example.com',
        'password': 'password123',
    }, follow_redirects=True)
    assert login_response.status_code == 200

    initial_response = client.get('/league_table')
    assert initial_response.status_code == 200
    assert b'Brighton' in initial_response.data
    assert b'class="form-cell win"' not in initial_response.data

    update_response = client.post('/admin/results', data={
        'matchweek': '1',
        'home_team': 'Arsenal',
        'away_team': 'Leicester',
        'result': 'home_win',
    }, follow_redirects=True)
    assert update_response.status_code == 200

    updated_response = client.get('/league_table')
    assert updated_response.status_code == 200
    assert b'class="form-cell win"' in updated_response.data


def test_submit_pick_page_allows_navigation_to_future_matchweeks(client):
    client.post('/register', data={
        'first_name': 'Morgan',
        'last_initial': 'S',
        'email': 'morgan@example.com',
        'phone': '07999999999',
        'password': 'password123',
        'confirm_password': 'password123',
    })
    client.post('/login', data={
        'email': 'morgan@example.com',
        'password': 'password123',
    })

    client.post('/competitions/create', data={
        'name': 'Navigation League',
        'code': 'NAV001',
        'start_matchweek': '1',
    }, follow_redirects=True)
    client.post('/competitions/join', data={'code': 'NAV001'}, follow_redirects=True)

    response = client.get('/submit_pick/1/2')
    assert response.status_code == 200
    assert b'/submit_pick/1/1' in response.data
    assert b'/submit_pick/1/3' in response.data


def test_upcoming_matchweek_helper_returns_next_pending_week():
    assert get_upcoming_matchweek(datetime(2026, 8, 1, 12, 0)) == 1
    assert get_upcoming_matchweek(datetime(2026, 8, 22, 12, 0)) == 2


def test_admin_can_submit_fixture_result(client):
    with app.app_context():
        user = User(name='Admin', email='admin@example.com', phone='01111111111', is_admin=True)
        user.set_password('password123')
        db.session.add(user)
        db.session.commit()

    with client.session_transaction() as session:
        session['_user_id'] = '1'
        session['_fresh'] = True

    response = client.post('/admin/results', data={
        'matchweek': '1',
        'home_team': 'Arsenal',
        'away_team': 'Coventry',
        'result': 'home_win',
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b'Fixture result saved' in response.data


def test_admin_results_buttons_show_exact_team_names(client):
    with app.app_context():
        user = User(name='Admin Labels', email='admin-labels@example.com', phone='01111111112', is_admin=True)
        user.set_password('password123')
        db.session.add(user)
        db.session.commit()

    login_response = client.post('/login', data={
        'email': 'admin-labels@example.com',
        'password': 'password123',
    }, follow_redirects=True)
    assert login_response.status_code == 200

    response = client.get('/admin/results')
    assert response.status_code == 200
    assert b'Arsenal' in response.data
    assert b'Coventry' in response.data
    assert b'Draw' in response.data
    assert b'Home win' not in response.data
    assert b'Away win' not in response.data


def test_admin_results_page_marks_selected_button(client):
    with app.app_context():
        user = User(name='Admin Selection', email='admin-selection@example.com', phone='01111111113', is_admin=True)
        user.set_password('password123')
        db.session.add(user)
        db.session.commit()

    client.post('/login', data={
        'email': 'admin-selection@example.com',
        'password': 'password123',
    }, follow_redirects=True)

    client.post('/admin/results', data={
        'matchweek': '1',
        'home_team': 'Arsenal',
        'away_team': 'Coventry',
        'result': 'home_win',
    }, follow_redirects=True)

    response = client.get('/admin/results')
    assert response.status_code == 200
    assert b'class="result-button selected"' in response.data


def test_admin_results_not_shown_in_nav_but_accessible_by_url(client):
    with app.app_context():
        user = User(name='Admin Hidden Link', email='admin-hidden@example.com', phone='01111111114', is_admin=True)
        user.set_password('password123')
        db.session.add(user)
        db.session.commit()

    login_response = client.post('/login', data={
        'email': 'admin-hidden@example.com',
        'password': 'password123',
    }, follow_redirects=True)
    assert login_response.status_code == 200

    page_response = client.get('/competitions')
    assert page_response.status_code == 200
    assert b'Admin Results' not in page_response.data

    direct_response = client.get('/admin/results')
    assert direct_response.status_code == 200


def test_fixtures_page_shows_team_rows_with_badges_and_tooltips(client):
    client.post('/register', data={
        'first_name': 'Sam',
        'last_initial': 'T',
        'email': 'sam@example.com',
        'phone': '07700000000',
        'password': 'password123',
        'confirm_password': 'password123',
    })
    client.post('/login', data={
        'email': 'sam@example.com',
        'password': 'password123',
    })

    response = client.get('/fixtures')
    assert response.status_code == 200
    assert b'fixture-scroll' in response.data
    assert b'fixture-badge-square' in response.data
    assert b'/files/arsenal.png' in response.data
    assert b'fixture-badge-tooltip' in response.data


def test_fixtures_page_shows_matchweek_labels_and_result_styles(client):
    client.post('/register', data={
        'first_name': 'Casey',
        'last_initial': 'P',
        'email': 'casey@example.com',
        'phone': '07700000001',
        'password': 'password123',
        'confirm_password': 'password123',
    })
    client.post('/login', data={
        'email': 'casey@example.com',
        'password': 'password123',
    })

    with app.app_context():
        from app import FixtureResult
        db.session.add(FixtureResult(matchweek=1, home_team='Arsenal', away_team='Coventry', result='home_win'))
        db.session.commit()

    response = client.get('/fixtures')
    assert response.status_code == 200
    assert b'MW1' not in response.data
    assert b'result-win' in response.data
    assert b'(next fixture)' in response.data


def test_register_blocks_duplicate_first_name_and_initial(client):
    first_response = client.post('/register', data={
        'first_name': 'Jamie',
        'last_initial': 'C',
        'email': 'jamie-c1@example.com',
        'phone': '07111111111',
        'password': 'password123',
        'confirm_password': 'password123',
    }, follow_redirects=True)
    assert first_response.status_code == 200

    client.get('/logout', follow_redirects=True)

    duplicate_response = client.post('/register', data={
        'first_name': 'Jamie',
        'last_initial': 'c',
        'email': 'jamie-c2@example.com',
        'phone': '07111111112',
        'password': 'password123',
        'confirm_password': 'password123',
    }, follow_redirects=True)
    assert duplicate_response.status_code == 200
    assert b'try something a little different' in duplicate_response.data.lower()


def test_competition_owner_can_manage_player_payment_status(client):
    client.post('/register', data={
        'first_name': 'Owner',
        'last_initial': 'A',
        'email': 'owner@example.com',
        'phone': '07222222221',
        'password': 'password123',
        'confirm_password': 'password123',
    })
    client.post('/login', data={
        'email': 'owner@example.com',
        'password': 'password123',
    })
    client.post('/competitions/create', data={
        'name': 'Payments League',
        'code': 'PAY001',
        'start_matchweek': '1',
    }, follow_redirects=True)
    client.post('/competitions/join', data={'code': 'PAY001'}, follow_redirects=True)

    client.get('/logout', follow_redirects=True)

    client.post('/register', data={
        'first_name': 'Player',
        'last_initial': 'B',
        'email': 'player@example.com',
        'phone': '07222222222',
        'password': 'password123',
        'confirm_password': 'password123',
    })
    client.post('/login', data={
        'email': 'player@example.com',
        'password': 'password123',
    })
    client.post('/competitions/join', data={'code': 'PAY001'}, follow_redirects=True)

    forbidden_response = client.get('/competitions/1/players', follow_redirects=True)
    assert forbidden_response.status_code == 200
    assert b'only the competition owner can view this page' in forbidden_response.data.lower()

    client.get('/logout', follow_redirects=True)
    client.post('/login', data={
        'email': 'owner@example.com',
        'password': 'password123',
    })

    manage_response = client.get('/competitions/1/players')
    assert manage_response.status_code == 200
    assert b'Unpaid' in manage_response.data

    toggle_response = client.post('/competitions/1/players/2/toggle_payment', follow_redirects=True)
    assert toggle_response.status_code == 200
    assert b'Paid' in toggle_response.data


def test_home_page_shows_payment_status_pill(client):
    from app import Competition, CompetitionMember, CompetitionPaymentStatus

    client.post('/register', data={
        'first_name': 'Pay',
        'last_initial': 'U',
        'email': 'pay-user@example.com',
        'phone': '07233333333',
        'password': 'password123',
        'confirm_password': 'password123',
    })
    client.post('/login', data={
        'email': 'pay-user@example.com',
        'password': 'password123',
    })

    client.post('/competitions/create', data={
        'name': 'Pay League',
        'code': 'PAYH01',
        'start_matchweek': '1',
    }, follow_redirects=True)
    client.post('/competitions/join', data={'code': 'PAYH01'}, follow_redirects=True)

    unpaid_response = client.get('/')
    assert unpaid_response.status_code == 200
    assert b'Payment Status' in unpaid_response.data
    assert b'Unpaid' in unpaid_response.data

    with app.app_context():
        competition = Competition.query.filter_by(code='PAYH01').first()
        membership = CompetitionMember.query.filter_by(competition_id=competition.id).first()
        payment = CompetitionPaymentStatus(competition_id=competition.id, user_id=membership.user_id, paid=True)
        db.session.add(payment)
        db.session.commit()

    paid_response = client.get('/')
    assert paid_response.status_code == 200
    assert b'Payment Status' not in paid_response.data
    assert b'Unpaid' not in paid_response.data


def test_submit_pick_cannot_amend_after_matchweek_started(client, monkeypatch):
    import app as app_module
    from app import Competition, Selection

    monkeypatch.setattr(app_module, 'FIXTURES_BY_MATCHWEEK', [
        {
            'matchweek': 1,
            'fixtures': [
                {
                    'home_team': 'Arsenal',
                    'away_team': 'Coventry',
                    'location': 'Test Ground',
                    'date': '01/01/2030 12:00',
                }
            ],
        }
    ])

    class ControlledDateTime:
        current_time = datetime(2029, 1, 1, 12, 0)

        @staticmethod
        def utcnow():
            return ControlledDateTime.current_time

        @staticmethod
        def strptime(value, fmt):
            return datetime.strptime(value, fmt)

    monkeypatch.setattr(app_module, 'datetime', ControlledDateTime)

    client.post('/register', data={
        'first_name': 'Lock',
        'last_initial': 'D',
        'email': 'lock@example.com',
        'phone': '07244444444',
        'password': 'password123',
        'confirm_password': 'password123',
    })
    client.post('/competitions/create', data={
        'name': 'Lock League',
        'code': 'LOCK01',
        'start_matchweek': '1',
    }, follow_redirects=True)
    client.post('/competitions/join', data={'code': 'LOCK01'}, follow_redirects=True)

    client.post('/submit_pick/1/1', data={'team': 'Arsenal'}, follow_redirects=True)

    ControlledDateTime.current_time = datetime(2031, 1, 2, 12, 0)

    response = client.post('/submit_pick/1/1', data={'team': 'Coventry'}, follow_redirects=True)
    assert response.status_code == 200
    assert b'can no longer be changed' in response.data

    with app.app_context():
        competition = Competition.query.filter_by(code='LOCK01').first()
        user = User.query.filter_by(email='lock@example.com').first()
        selection = Selection.query.filter_by(competition_id=competition.id, user_id=user.id, matchweek=1).first()
        assert selection.team_name == 'Arsenal'


def test_missing_pick_auto_assigns_first_unused_alphabetical_team(client, monkeypatch):
    import app as app_module
    from app import Competition, Selection, resolve_completed_matchweeks_for_competition

    monkeypatch.setattr(app_module, 'FIXTURES_BY_MATCHWEEK', [
        {
            'matchweek': 1,
            'fixtures': [
                {
                    'home_team': 'Arsenal',
                    'away_team': 'Coventry',
                    'location': 'Test Ground A',
                    'date': '01/01/2030 12:00',
                }
            ],
        },
        {
            'matchweek': 2,
            'fixtures': [
                {
                    'home_team': 'Arsenal',
                    'away_team': 'Chelsea',
                    'location': 'Test Ground B',
                    'date': '08/01/2030 12:00',
                }
            ],
        },
    ])

    client.post('/register', data={
        'first_name': 'Auto',
        'last_initial': 'P',
        'email': 'auto@example.com',
        'phone': '07255555555',
        'password': 'password123',
        'confirm_password': 'password123',
    })
    client.post('/competitions/create', data={
        'name': 'Auto League',
        'code': 'AUTO01',
        'start_matchweek': '1',
    }, follow_redirects=True)
    client.post('/competitions/join', data={'code': 'AUTO01'}, follow_redirects=True)

    client.post('/submit_pick/1/1', data={'team': 'Arsenal'}, follow_redirects=True)

    with app.app_context():
        competition = Competition.query.filter_by(code='AUTO01').first()
        user = User.query.filter_by(email='auto@example.com').first()

        resolve_completed_matchweeks_for_competition(competition, now=datetime(2031, 1, 9, 12, 0))

        auto_selection = Selection.query.filter_by(competition_id=competition.id, user_id=user.id, matchweek=2).first()
        assert auto_selection is not None
        assert auto_selection.team_name == 'Chelsea'


def test_view_selections_shows_submission_state_then_reveals_badges(client, monkeypatch):
    from app import Competition, CompetitionMember, Selection

    client.post('/register', data={
        'first_name': 'Aaron',
        'last_initial': 'A',
        'email': 'aaron@example.com',
        'phone': '07333333331',
        'password': 'password123',
        'confirm_password': 'password123',
    })
    client.post('/competitions/create', data={
        'name': 'Selections League',
        'code': 'SEL001',
        'start_matchweek': '1',
    }, follow_redirects=True)
    client.post('/competitions/join', data={'code': 'SEL001'}, follow_redirects=True)

    client.get('/logout', follow_redirects=True)

    client.post('/register', data={
        'first_name': 'Bella',
        'last_initial': 'B',
        'email': 'bella@example.com',
        'phone': '07333333332',
        'password': 'password123',
        'confirm_password': 'password123',
    })
    client.post('/competitions/join', data={'code': 'SEL001'}, follow_redirects=True)

    with app.app_context():
        user_a = User.query.filter_by(email='aaron@example.com').first()
        user_b = User.query.filter_by(email='bella@example.com').first()
        competition = Competition.query.filter_by(code='SEL001').first()

        member_a = CompetitionMember.query.filter_by(competition_id=competition.id, user_id=user_a.id).first()
        member_b = CompetitionMember.query.filter_by(competition_id=competition.id, user_id=user_b.id).first()
        member_a.lives = 3
        member_b.lives = 1

        db.session.add(Selection(competition_id=competition.id, user_id=user_a.id, matchweek=1, team_name='Arsenal'))
        db.session.commit()

    import app as app_module

    class PreDeadlineDateTime:
        @staticmethod
        def utcnow():
            return datetime(2026, 7, 16, 12, 0)

        @staticmethod
        def strptime(value, fmt):
            return datetime.strptime(value, fmt)

    monkeypatch.setattr(app_module, 'datetime', PreDeadlineDateTime)

    pre_deadline = client.get('/view_selections/1/1')
    assert pre_deadline.status_code == 200
    assert b'fixture-badge-square' not in pre_deadline.data
    assert b'\xe2\x9c\x93' in pre_deadline.data
    assert b'selection-status not-submitted' in pre_deadline.data
    assert b'lives-3' in pre_deadline.data
    assert pre_deadline.data.count(b'lives-3') >= 2

    class PostDeadlineDateTime:
        @staticmethod
        def utcnow():
            return datetime(2027, 1, 1, 0, 0)

        @staticmethod
        def strptime(value, fmt):
            return datetime.strptime(value, fmt)

    monkeypatch.setattr(app_module, 'datetime', PostDeadlineDateTime)

    post_deadline = client.get('/view_selections/1/1')
    assert post_deadline.status_code == 200
    assert b'fixture-badge-square' in post_deadline.data
    assert b'Arsenal vs Coventry' in post_deadline.data


def test_view_selections_future_week_url_does_not_reveal_earlier_picks(client, monkeypatch):
    import app as app_module
    from app import Selection

    monkeypatch.setattr(app_module, 'FIXTURES_BY_MATCHWEEK', [
        {
            'matchweek': 1,
            'fixtures': [
                {
                    'home_team': 'Arsenal',
                    'away_team': 'Coventry',
                    'location': 'Test Ground A',
                    'date': '01/01/2030 12:00',
                }
            ],
        },
        {
            'matchweek': 2,
            'fixtures': [
                {
                    'home_team': 'Chelsea',
                    'away_team': 'Brighton',
                    'location': 'Test Ground B',
                    'date': '08/01/2030 12:00',
                }
            ],
        },
    ])

    class BeforeWeekOneDeadlineDateTime:
        @staticmethod
        def utcnow():
            # Before week 1 deadline (kickoff minus 1 hour).
            return datetime(2030, 1, 1, 10, 0)

        @staticmethod
        def strptime(value, fmt):
            return datetime.strptime(value, fmt)

    monkeypatch.setattr(app_module, 'datetime', BeforeWeekOneDeadlineDateTime)

    client.post('/register', data={
        'first_name': 'Future',
        'last_initial': 'U',
        'email': 'future-url@example.com',
        'phone': '07333339999',
        'password': 'password123',
        'confirm_password': 'password123',
    })
    client.post('/competitions/create', data={
        'name': 'Future URL League',
        'code': 'FURL01',
        'start_matchweek': '1',
    }, follow_redirects=True)
    client.post('/competitions/join', data={'code': 'FURL01'}, follow_redirects=True)
    client.post('/submit_pick/1/1', data={'team': 'Arsenal'}, follow_redirects=True)

    response = client.get('/view_selections/1/2')
    assert response.status_code == 200
    assert b'fixture-badge-square' not in response.data
    assert b'Arsenal vs Coventry' not in response.data


def test_view_selections_reduces_life_for_losing_pick(client, monkeypatch):
    import app as app_module
    from app import Competition, CompetitionMember, FixtureResult, Selection

    monkeypatch.setattr(app_module, 'FIXTURES_BY_MATCHWEEK', [
        {
            'matchweek': 1,
            'fixtures': [
                {
                    'home_team': 'Arsenal',
                    'away_team': 'Coventry',
                    'location': 'Test Ground',
                    'date': '01/01/2026 12:00',
                }
            ],
        }
    ])

    client.post('/register', data={
        'first_name': 'Luca',
        'last_initial': 'T',
        'email': 'luca@example.com',
        'phone': '07444444444',
        'password': 'password123',
        'confirm_password': 'password123',
    })
    client.post('/competitions/create', data={
        'name': 'Life Loss League',
        'code': 'LIFE01',
        'start_matchweek': '1',
    }, follow_redirects=True)
    client.post('/competitions/join', data={'code': 'LIFE01'}, follow_redirects=True)
    client.post('/submit_pick/1/1', data={'team': 'Arsenal'}, follow_redirects=True)

    with app.app_context():
        competition = Competition.query.filter_by(code='LIFE01').first()
        user = User.query.filter_by(email='luca@example.com').first()
        member = CompetitionMember.query.filter_by(competition_id=competition.id, user_id=user.id).first()
        assert member.lives == 3

        db.session.add(FixtureResult(matchweek=1, home_team='Arsenal', away_team='Coventry', result='away_win'))
        db.session.commit()

    class PostResultDateTime:
        @staticmethod
        def utcnow():
            return datetime(2026, 1, 2, 12, 0)

        @staticmethod
        def strptime(value, fmt):
            return datetime.strptime(value, fmt)

    monkeypatch.setattr(app_module, 'datetime', PostResultDateTime)

    response = client.get('/view_selections/1/1')
    assert response.status_code == 200

    with app.app_context():
        competition = Competition.query.filter_by(code='LIFE01').first()
        user = User.query.filter_by(email='luca@example.com').first()
        member = CompetitionMember.query.filter_by(competition_id=competition.id, user_id=user.id).first()
        selection = Selection.query.filter_by(competition_id=competition.id, user_id=user.id, matchweek=1).first()

        assert selection is not None
        assert member.lives == 2


def test_view_selections_reduces_life_before_kickoff_when_results_entered(client, monkeypatch):
    import app as app_module
    from app import Competition, CompetitionMember, FixtureResult

    monkeypatch.setattr(app_module, 'FIXTURES_BY_MATCHWEEK', [
        {
            'matchweek': 1,
            'fixtures': [
                {
                    'home_team': 'Arsenal',
                    'away_team': 'Coventry',
                    'location': 'Test Ground',
                    'date': '31/12/2030 12:00',
                }
            ],
        }
    ])

    client.post('/register', data={
        'first_name': 'Mila',
        'last_initial': 'Q',
        'email': 'mila@example.com',
        'phone': '07555555555',
        'password': 'password123',
        'confirm_password': 'password123',
    })
    client.post('/competitions/create', data={
        'name': 'Early Result League',
        'code': 'EARLY1',
        'start_matchweek': '1',
    }, follow_redirects=True)
    client.post('/competitions/join', data={'code': 'EARLY1'}, follow_redirects=True)
    client.post('/submit_pick/1/1', data={'team': 'Arsenal'}, follow_redirects=True)

    with app.app_context():
        competition = Competition.query.filter_by(code='EARLY1').first()
        user = User.query.filter_by(email='mila@example.com').first()
        member = CompetitionMember.query.filter_by(competition_id=competition.id, user_id=user.id).first()
        assert member.lives == 3

        db.session.add(FixtureResult(matchweek=1, home_team='Arsenal', away_team='Coventry', result='away_win'))
        db.session.commit()

    class BeforeKickoffDateTime:
        @staticmethod
        def utcnow():
            return datetime(2029, 1, 1, 12, 0)

        @staticmethod
        def strptime(value, fmt):
            return datetime.strptime(value, fmt)

    monkeypatch.setattr(app_module, 'datetime', BeforeKickoffDateTime)

    response = client.get('/view_selections/1/1')
    assert response.status_code == 200

    with app.app_context():
        competition = Competition.query.filter_by(code='EARLY1').first()
        user = User.query.filter_by(email='mila@example.com').first()
        member = CompetitionMember.query.filter_by(competition_id=competition.id, user_id=user.id).first()
        assert member.lives == 2


def test_result_correction_recalculates_lives(client, monkeypatch):
    import app as app_module
    from app import Competition, CompetitionMember, FixtureResult

    monkeypatch.setattr(app_module, 'FIXTURES_BY_MATCHWEEK', [
        {
            'matchweek': 1,
            'fixtures': [
                {
                    'home_team': 'Arsenal',
                    'away_team': 'Coventry',
                    'location': 'Test Ground',
                    'date': '01/01/2026 12:00',
                }
            ],
        }
    ])

    client.post('/register', data={
        'first_name': 'Nora',
        'last_initial': 'V',
        'email': 'nora@example.com',
        'phone': '07666666666',
        'password': 'password123',
        'confirm_password': 'password123',
    })
    client.post('/competitions/create', data={
        'name': 'Correction League',
        'code': 'CORR01',
        'start_matchweek': '1',
    }, follow_redirects=True)
    client.post('/competitions/join', data={'code': 'CORR01'}, follow_redirects=True)
    client.post('/submit_pick/1/1', data={'team': 'Arsenal'}, follow_redirects=True)

    with app.app_context():
        db.session.add(FixtureResult(matchweek=1, home_team='Arsenal', away_team='Coventry', result='home_win'))
        db.session.commit()

    class PostKickoffDateTime:
        @staticmethod
        def utcnow():
            return datetime(2026, 1, 2, 12, 0)

        @staticmethod
        def strptime(value, fmt):
            return datetime.strptime(value, fmt)

    monkeypatch.setattr(app_module, 'datetime', PostKickoffDateTime)

    first_view = client.get('/view_selections/1/1')
    assert first_view.status_code == 200

    with app.app_context():
        competition = Competition.query.filter_by(code='CORR01').first()
        user = User.query.filter_by(email='nora@example.com').first()
        member = CompetitionMember.query.filter_by(competition_id=competition.id, user_id=user.id).first()
        assert member.lives == 3

    client.post('/admin/results', data={
        'matchweek': '1',
        'home_team': 'Arsenal',
        'away_team': 'Coventry',
        'result': 'away_win',
    }, follow_redirects=True)

    second_view = client.get('/view_selections/1/1')
    assert second_view.status_code == 200

    with app.app_context():
        competition = Competition.query.filter_by(code='CORR01').first()
        user = User.query.filter_by(email='nora@example.com').first()
        member = CompetitionMember.query.filter_by(competition_id=competition.id, user_id=user.id).first()
        assert member.lives == 2

    # Correcting back to a win must restore the deducted life immediately.
    client.post('/admin/results', data={
        'matchweek': '1',
        'home_team': 'Arsenal',
        'away_team': 'Coventry',
        'result': 'home_win',
    }, follow_redirects=True)

    third_view = client.get('/view_selections/1/1')
    assert third_view.status_code == 200

    with app.app_context():
        competition = Competition.query.filter_by(code='CORR01').first()
        user = User.query.filter_by(email='nora@example.com').first()
        member = CompetitionMember.query.filter_by(competition_id=competition.id, user_id=user.id).first()
        assert member.lives == 3


def test_draw_pick_costs_one_life(client, monkeypatch):
    import app as app_module
    from app import Competition, CompetitionMember, FixtureResult

    monkeypatch.setattr(app_module, 'FIXTURES_BY_MATCHWEEK', [
        {
            'matchweek': 1,
            'fixtures': [
                {
                    'home_team': 'Arsenal',
                    'away_team': 'Coventry',
                    'location': 'Test Ground',
                    'date': '01/01/2026 12:00',
                }
            ],
        }
    ])

    client.post('/register', data={
        'first_name': 'Drew',
        'last_initial': 'Y',
        'email': 'drew@example.com',
        'phone': '07666666667',
        'password': 'password123',
        'confirm_password': 'password123',
    })
    client.post('/competitions/create', data={
        'name': 'Draw League',
        'code': 'DRAW01',
        'start_matchweek': '1',
    }, follow_redirects=True)
    client.post('/competitions/join', data={'code': 'DRAW01'}, follow_redirects=True)
    client.post('/submit_pick/1/1', data={'team': 'Arsenal'}, follow_redirects=True)

    with app.app_context():
        db.session.add(FixtureResult(matchweek=1, home_team='Arsenal', away_team='Coventry', result='draw'))
        db.session.commit()

    class PostKickoffDateTime:
        @staticmethod
        def utcnow():
            return datetime(2026, 1, 2, 12, 0)

        @staticmethod
        def strptime(value, fmt):
            return datetime.strptime(value, fmt)

    monkeypatch.setattr(app_module, 'datetime', PostKickoffDateTime)

    response = client.get('/view_selections/1/1')
    assert response.status_code == 200

    with app.app_context():
        competition = Competition.query.filter_by(code='DRAW01').first()
        user = User.query.filter_by(email='drew@example.com').first()
        member = CompetitionMember.query.filter_by(competition_id=competition.id, user_id=user.id).first()
        assert member.lives == 2


def test_lives_recalculation_is_idempotent(client, monkeypatch):
    import app as app_module
    from app import Competition, CompetitionMember, FixtureResult, resolve_completed_matchweeks_for_competition

    monkeypatch.setattr(app_module, 'FIXTURES_BY_MATCHWEEK', [
        {
            'matchweek': 1,
            'fixtures': [
                {
                    'home_team': 'Arsenal',
                    'away_team': 'Coventry',
                    'location': 'Test Ground',
                    'date': '01/01/2026 12:00',
                }
            ],
        }
    ])

    client.post('/register', data={
        'first_name': 'Ida',
        'last_initial': 'M',
        'email': 'ida@example.com',
        'phone': '07666666668',
        'password': 'password123',
        'confirm_password': 'password123',
    })
    client.post('/competitions/create', data={
        'name': 'Idempotent League',
        'code': 'IDEM01',
        'start_matchweek': '1',
    }, follow_redirects=True)
    client.post('/competitions/join', data={'code': 'IDEM01'}, follow_redirects=True)
    client.post('/submit_pick/1/1', data={'team': 'Arsenal'}, follow_redirects=True)

    with app.app_context():
        db.session.add(FixtureResult(matchweek=1, home_team='Arsenal', away_team='Coventry', result='away_win'))
        db.session.commit()

    class PostKickoffDateTime:
        @staticmethod
        def utcnow():
            return datetime(2026, 1, 2, 12, 0)

        @staticmethod
        def strptime(value, fmt):
            return datetime.strptime(value, fmt)

    monkeypatch.setattr(app_module, 'datetime', PostKickoffDateTime)

    with app.app_context():
        competition = Competition.query.filter_by(code='IDEM01').first()
        user = User.query.filter_by(email='ida@example.com').first()

        resolve_completed_matchweeks_for_competition(competition)
        first_value = CompetitionMember.query.filter_by(competition_id=competition.id, user_id=user.id).first().lives

        resolve_completed_matchweeks_for_competition(competition)
        second_value = CompetitionMember.query.filter_by(competition_id=competition.id, user_id=user.id).first().lives

        assert first_value == 2
        assert second_value == 2


def test_admin_result_corrections_restore_all_lives(client, monkeypatch):
    import app as app_module
    from app import Competition, CompetitionMember, User

    monkeypatch.setattr(app_module, 'FIXTURES_BY_MATCHWEEK', [
        {
            'matchweek': 1,
            'fixtures': [
                {
                    'home_team': 'Arsenal',
                    'away_team': 'Coventry',
                    'location': 'Test Ground A',
                    'date': '01/01/2026 12:00',
                }
            ],
        },
        {
            'matchweek': 2,
            'fixtures': [
                {
                    'home_team': 'Liverpool',
                    'away_team': "Nott'm Forest",
                    'location': 'Test Ground B',
                    'date': '08/01/2026 12:00',
                }
            ],
        },
    ])

    class PostKickoffDateTime:
        @staticmethod
        def utcnow():
            return datetime(2026, 1, 9, 12, 0)

        @staticmethod
        def strptime(value, fmt):
            return datetime.strptime(value, fmt)

    monkeypatch.setattr(app_module, 'datetime', PostKickoffDateTime)

    client.post('/register', data={
        'first_name': 'Admin',
        'last_initial': 'Z',
        'email': 'admin-corrections@example.com',
        'phone': '07666666669',
        'password': 'password123',
        'confirm_password': 'password123',
    })
    client.post('/competitions/create', data={
        'name': 'Restore League',
        'code': 'REST01',
        'start_matchweek': '1',
    }, follow_redirects=True)
    client.post('/competitions/join', data={'code': 'REST01'}, follow_redirects=True)
    client.post('/submit_pick/1/1', data={'team': 'Arsenal'}, follow_redirects=True)
    client.post('/submit_pick/1/2', data={'team': 'Liverpool'}, follow_redirects=True)

    client.post('/admin/results', data={
        'matchweek': '1',
        'home_team': 'Arsenal',
        'away_team': 'Coventry',
        'result': 'away_win',
    }, follow_redirects=True)
    client.post('/admin/results', data={
        'matchweek': '2',
        'home_team': 'Liverpool',
        'away_team': "Nott'm Forest",
        'result': 'away_win',
    }, follow_redirects=True)

    with app.app_context():
        db.session.remove()
        competition = Competition.query.filter_by(code='REST01').first()
        user = User.query.filter_by(email='admin-corrections@example.com').first()
        member = CompetitionMember.query.filter_by(competition_id=competition.id, user_id=user.id).first()
        assert member.lives == 1

    client.post('/admin/results', data={
        'matchweek': '1',
        'home_team': 'Arsenal',
        'away_team': 'Coventry',
        'result': 'home_win',
    }, follow_redirects=True)
    client.post('/admin/results', data={
        'matchweek': '2',
        'home_team': 'Liverpool',
        'away_team': "Nott'm Forest",
        'result': 'home_win',
    }, follow_redirects=True)

    with app.app_context():
        db.session.remove()
        competition = Competition.query.filter_by(code='REST01').first()
        user = User.query.filter_by(email='admin-corrections@example.com').first()
        member = CompetitionMember.query.filter_by(competition_id=competition.id, user_id=user.id).first()
        assert member.lives == 3


def test_life_reduces_when_pick_fixture_result_exists_in_partial_week(client, monkeypatch):
    import app as app_module
    from app import Competition, CompetitionMember, FixtureResult

    monkeypatch.setattr(app_module, 'FIXTURES_BY_MATCHWEEK', [
        {
            'matchweek': 1,
            'fixtures': [
                {
                    'home_team': 'Arsenal',
                    'away_team': 'Coventry',
                    'location': 'Test Ground A',
                    'date': '01/01/2026 12:00',
                },
                {
                    'home_team': 'Chelsea',
                    'away_team': 'Brighton',
                    'location': 'Test Ground B',
                    'date': '01/01/2026 15:00',
                },
            ],
        }
    ])

    client.post('/register', data={
        'first_name': 'Owen',
        'last_initial': 'Z',
        'email': 'owen@example.com',
        'phone': '07777770000',
        'password': 'password123',
        'confirm_password': 'password123',
    })
    client.post('/competitions/create', data={
        'name': 'Partial Week League',
        'code': 'PART01',
        'start_matchweek': '1',
    }, follow_redirects=True)
    client.post('/competitions/join', data={'code': 'PART01'}, follow_redirects=True)
    client.post('/submit_pick/1/1', data={'team': 'Arsenal'}, follow_redirects=True)

    with app.app_context():
        db.session.add(FixtureResult(matchweek=1, home_team='Arsenal', away_team='Coventry', result='away_win'))
        db.session.commit()

    class AfterKickoffDateTime:
        @staticmethod
        def utcnow():
            return datetime(2026, 1, 2, 12, 0)

        @staticmethod
        def strptime(value, fmt):
            return datetime.strptime(value, fmt)

    monkeypatch.setattr(app_module, 'datetime', AfterKickoffDateTime)

    response = client.get('/view_selections/1/1')
    assert response.status_code == 200

    with app.app_context():
        competition = Competition.query.filter_by(code='PART01').first()
        user = User.query.filter_by(email='owen@example.com').first()
        member = CompetitionMember.query.filter_by(competition_id=competition.id, user_id=user.id).first()
        assert member.lives == 2


def test_life_reduces_before_kickoff_in_partial_week_with_pick_result(client, monkeypatch):
    import app as app_module
    from app import Competition, CompetitionMember, FixtureResult

    monkeypatch.setattr(app_module, 'FIXTURES_BY_MATCHWEEK', [
        {
            'matchweek': 1,
            'fixtures': [
                {
                    'home_team': 'Arsenal',
                    'away_team': 'Coventry',
                    'location': 'Test Ground A',
                    'date': '31/12/2030 12:00',
                },
                {
                    'home_team': 'Chelsea',
                    'away_team': 'Brighton',
                    'location': 'Test Ground B',
                    'date': '31/12/2030 15:00',
                },
            ],
        }
    ])

    client.post('/register', data={
        'first_name': 'Iris',
        'last_initial': 'D',
        'email': 'iris@example.com',
        'phone': '07777770001',
        'password': 'password123',
        'confirm_password': 'password123',
    })
    client.post('/competitions/create', data={
        'name': 'Pre Kick Partial League',
        'code': 'PART02',
        'start_matchweek': '1',
    }, follow_redirects=True)
    client.post('/competitions/join', data={'code': 'PART02'}, follow_redirects=True)
    client.post('/submit_pick/1/1', data={'team': 'Arsenal'}, follow_redirects=True)

    with app.app_context():
        db.session.add(FixtureResult(matchweek=1, home_team='Arsenal', away_team='Coventry', result='away_win'))
        db.session.commit()

    class BeforeKickoffDateTime:
        @staticmethod
        def utcnow():
            return datetime(2029, 1, 1, 12, 0)

        @staticmethod
        def strptime(value, fmt):
            return datetime.strptime(value, fmt)

    monkeypatch.setattr(app_module, 'datetime', BeforeKickoffDateTime)

    response = client.get('/view_selections/1/1')
    assert response.status_code == 200

    with app.app_context():
        competition = Competition.query.filter_by(code='PART02').first()
        user = User.query.filter_by(email='iris@example.com').first()
        member = CompetitionMember.query.filter_by(competition_id=competition.id, user_id=user.id).first()
        assert member.lives == 2


def _register_and_login(client, email, phone, first_name='Push', last_initial='N'):
    client.post('/register', data={
        'first_name': first_name,
        'last_initial': last_initial,
        'email': email,
        'phone': phone,
        'password': 'password123',
        'confirm_password': 'password123',
    })
    client.post('/login', data={'email': email, 'password': 'password123'})


def test_notifications_page_requires_login(client):
    response = client.get('/notifications', follow_redirects=True)
    assert response.status_code == 200
    assert b'Login' in response.data


def test_notifications_routes_hidden_when_feature_disabled(client, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, 'NOTIFICATIONS_ENABLED', False)

    _register_and_login(client, 'featureoff@example.com', '07333331111')

    response = client.get('/notifications')
    assert response.status_code == 404


def test_notifications_nav_visible_when_feature_enabled(client, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, 'NOTIFICATIONS_ENABLED', True)

    _register_and_login(client, 'featureon@example.com', '07333332222')

    response = client.get('/competitions')
    assert response.status_code == 200
    assert b'Notifications' in response.data


def test_notifications_preferences_save_and_reload(client, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, 'NOTIFICATIONS_ENABLED', True)

    _register_and_login(client, 'notify@example.com', '07333330000')

    response = client.get('/notifications')
    assert response.status_code == 200
    assert b'48 hour deadline warning' in response.data

    save_response = client.post('/notifications', data={
        'deadline_48h': 'on',
        'results_confirmed': 'on',
    }, follow_redirects=True)
    assert save_response.status_code == 200
    assert b'Notification preferences saved' in save_response.data

    with app.app_context():
        from app import NotificationPreference

        user = User.query.filter_by(email='notify@example.com').first()
        preference = NotificationPreference.query.filter_by(user_id=user.id).first()
        assert preference.deadline_48h is True
        assert preference.deadline_24h is False
        assert preference.deadline_passed is False
        assert preference.results_confirmed is True


def test_notifications_subscribe_endpoint_persists_subscription(client, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, 'NOTIFICATIONS_ENABLED', True)

    _register_and_login(client, 'subscriber@example.com', '07333330001')

    response = client.post('/notifications/subscribe', json={
        'endpoint': 'https://push.example.com/endpoint-1',
        'keys': {'p256dh': 'test-p256dh', 'auth': 'test-auth'},
    })
    assert response.status_code == 200

    with app.app_context():
        from app import PushSubscription

        user = User.query.filter_by(email='subscriber@example.com').first()
        subscription = PushSubscription.query.filter_by(endpoint='https://push.example.com/endpoint-1').first()
        assert subscription is not None
        assert subscription.user_id == user.id
        assert subscription.p256dh == 'test-p256dh'
        assert subscription.auth == 'test-auth'


def test_notifications_subscribe_rejects_invalid_payload(client, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, 'NOTIFICATIONS_ENABLED', True)

    _register_and_login(client, 'badpayload@example.com', '07333330002')

    response = client.post('/notifications/subscribe', json={'endpoint': ''})
    assert response.status_code == 400


def test_check_and_send_deadline_notifications_sends_and_dedupes(client, monkeypatch):
    import app as app_module
    from app import (
        Competition,
        CompetitionMember,
        NotificationLog,
        NotificationPreference,
        PushSubscription,
        check_and_send_deadline_notifications,
    )

    monkeypatch.setattr(app_module, 'NOTIFICATIONS_ENABLED', True)
    monkeypatch.setattr(app_module, 'FIXTURES_BY_MATCHWEEK', [
        {
            'matchweek': 1,
            'fixtures': [
                {
                    'home_team': 'Arsenal',
                    'away_team': 'Coventry',
                    'location': 'Test Ground',
                    'date': '10/01/2030 12:00',
                }
            ],
        }
    ])

    sent_calls = []

    def fake_webpush(**kwargs):
        sent_calls.append(kwargs)

    monkeypatch.setattr(app_module, 'webpush', fake_webpush)

    _register_and_login(client, 'reminder@example.com', '07333330003')
    client.post('/competitions/create', data={
        'name': 'Reminder League',
        'code': 'REMIND1',
        'start_matchweek': '1',
    }, follow_redirects=True)
    client.post('/competitions/join', data={'code': 'REMIND1'}, follow_redirects=True)

    with app.app_context():
        user = User.query.filter_by(email='reminder@example.com').first()
        db.session.add(NotificationPreference(user_id=user.id, deadline_48h=True))
        db.session.add(PushSubscription(
            user_id=user.id,
            endpoint='https://push.example.com/reminder-endpoint',
            p256dh='p256dh-value',
            auth='auth-value',
        ))
        db.session.commit()

    # 48 hours before the (kickoff - 1hr) deadline for the 10/01/2030 12:00 kickoff.
    check_and_send_deadline_notifications(now=datetime(2030, 1, 8, 11, 30))

    assert len(sent_calls) == 1

    with app.app_context():
        competition = Competition.query.filter_by(code='REMIND1').first()
        user = User.query.filter_by(email='reminder@example.com').first()
        log_entries = NotificationLog.query.filter_by(
            user_id=user.id, competition_id=competition.id, matchweek=1, notification_type='deadline_48h',
        ).all()
        assert len(log_entries) == 1

    # Calling again for the same window must not send a duplicate notification.
    check_and_send_deadline_notifications(now=datetime(2030, 1, 8, 11, 45))
    assert len(sent_calls) == 1


def test_check_and_send_deadline_notifications_works_without_request_context(client, monkeypatch):
    import app as app_module
    from app import (
        Competition,
        CompetitionMember,
        NotificationPreference,
        PushSubscription,
        check_and_send_deadline_notifications,
    )

    monkeypatch.setattr(app_module, 'NOTIFICATIONS_ENABLED', True)
    monkeypatch.setattr(app_module, 'FIXTURES_BY_MATCHWEEK', [
        {
            'matchweek': 1,
            'fixtures': [
                {
                    'home_team': 'Arsenal',
                    'away_team': 'Coventry',
                    'location': 'Test Ground',
                    'date': '10/01/2030 12:00',
                }
            ],
        }
    ])

    sent_calls = []
    monkeypatch.setattr(app_module, 'webpush', lambda **kwargs: sent_calls.append(kwargs))

    with app.app_context():
        user = User(name='Background Push', email='background@example.com', phone='07333330009', is_admin=True)
        user.set_password('password123')
        db.session.add(user)
        db.session.commit()

        competition = Competition(name='Background League', code='BGRND1', admin_id=user.id, start_matchweek=1)
        db.session.add(competition)
        db.session.commit()

        db.session.add(CompetitionMember(competition_id=competition.id, user_id=user.id, lives=3, active=True))
        db.session.add(NotificationPreference(user_id=user.id, deadline_24h=True))
        db.session.add(PushSubscription(
            user_id=user.id,
            endpoint='https://push.example.com/background-endpoint',
            p256dh='p256dh-value',
            auth='auth-value',
        ))
        db.session.commit()

        check_and_send_deadline_notifications(now=datetime(2030, 1, 9, 11, 5))

    assert len(sent_calls) == 1


def test_deadline_reminder_skipped_if_pick_already_submitted(client, monkeypatch):
    import app as app_module
    from app import NotificationPreference, PushSubscription, check_and_send_deadline_notifications

    monkeypatch.setattr(app_module, 'NOTIFICATIONS_ENABLED', True)
    monkeypatch.setattr(app_module, 'FIXTURES_BY_MATCHWEEK', [
        {
            'matchweek': 1,
            'fixtures': [
                {
                    'home_team': 'Arsenal',
                    'away_team': 'Coventry',
                    'location': 'Test Ground',
                    'date': '10/01/2030 12:00',
                }
            ],
        }
    ])

    sent_calls = []
    monkeypatch.setattr(app_module, 'webpush', lambda **kwargs: sent_calls.append(kwargs))

    _register_and_login(client, 'alreadypicked@example.com', '07333330004')
    client.post('/competitions/create', data={
        'name': 'Already Picked League',
        'code': 'PICKED1',
        'start_matchweek': '1',
    }, follow_redirects=True)
    client.post('/competitions/join', data={'code': 'PICKED1'}, follow_redirects=True)
    client.post('/submit_pick/1/1', data={'team': 'Arsenal'}, follow_redirects=True)

    with app.app_context():
        user = User.query.filter_by(email='alreadypicked@example.com').first()
        db.session.add(NotificationPreference(user_id=user.id, deadline_48h=True))
        db.session.add(PushSubscription(
            user_id=user.id,
            endpoint='https://push.example.com/picked-endpoint',
            p256dh='p256dh-value',
            auth='auth-value',
        ))
        db.session.commit()

    check_and_send_deadline_notifications(now=datetime(2030, 1, 8, 11, 30))

    assert sent_calls == []


def test_admin_results_triggers_results_confirmed_notification(client, monkeypatch):
    import app as app_module
    from app import Competition, NotificationLog, NotificationPreference, PushSubscription

    monkeypatch.setattr(app_module, 'NOTIFICATIONS_ENABLED', True)
    monkeypatch.setattr(app_module, 'FIXTURES_BY_MATCHWEEK', [
        {
            'matchweek': 1,
            'fixtures': [
                {
                    'home_team': 'Arsenal',
                    'away_team': 'Coventry',
                    'location': 'Test Ground',
                    'date': '01/01/2026 12:00',
                }
            ],
        }
    ])

    sent_calls = []
    monkeypatch.setattr(app_module, 'webpush', lambda **kwargs: sent_calls.append(kwargs))

    _register_and_login(client, 'resultsfan@example.com', '07333330005')
    client.post('/competitions/create', data={
        'name': 'Results League',
        'code': 'RESULT1',
        'start_matchweek': '1',
    }, follow_redirects=True)
    client.post('/competitions/join', data={'code': 'RESULT1'}, follow_redirects=True)
    client.post('/submit_pick/1/1', data={'team': 'Arsenal'}, follow_redirects=True)

    with app.app_context():
        user = User.query.filter_by(email='resultsfan@example.com').first()
        db.session.add(NotificationPreference(user_id=user.id, results_confirmed=True))
        db.session.add(PushSubscription(
            user_id=user.id,
            endpoint='https://push.example.com/results-endpoint',
            p256dh='p256dh-value',
            auth='auth-value',
        ))
        db.session.commit()

    client.post('/admin/results', data={
        'matchweek': '1',
        'home_team': 'Arsenal',
        'away_team': 'Coventry',
        'result': 'home_win',
    }, follow_redirects=True)

    assert len(sent_calls) == 1

    with app.app_context():
        competition = Competition.query.filter_by(code='RESULT1').first()
        user = User.query.filter_by(email='resultsfan@example.com').first()
        log_entry = NotificationLog.query.filter_by(
            user_id=user.id, competition_id=competition.id, matchweek=1, notification_type='results_confirmed',
        ).first()
        assert log_entry is not None
