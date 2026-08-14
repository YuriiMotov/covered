from app import application, main


def test_main_exposes_the_fastapi_app():
    assert main.app is application.app
