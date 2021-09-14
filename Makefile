lint:
	pipenv run black --line-length 120 .
	pipenv run isort --line-length 120 --profile black .
	pipenv run flake8
	pipenv run mypy uploader.py

test:
	docker build -t sftp-test .
	docker run --rm -v $(shell pwd):/app -w /app --name sftp -it sftp-test

