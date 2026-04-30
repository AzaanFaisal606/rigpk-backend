.PHONY: dev-backend dev-frontend scrape test

dev-backend:
	cd backend && uvicorn main:app --reload

dev-frontend:
	cd frontend && npm run dev

scrape:
	python run_all.py

test:
	python -m pytest tests/
