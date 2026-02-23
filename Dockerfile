# Use official Python runtime as a parent image
# Updated: 2026-02-23
FROM python:3.14-slim

# Set working directory
WORKDIR /app

# Install git, poetry
RUN apt-get update && apt-get install -y git && apt-get clean
RUN pip install poetry

# Copy project definition
COPY pyproject.toml poetry.lock* ./

# Configure poetry to not use virtualenvs
RUN poetry config virtualenvs.create false \
    && poetry install --only main --no-interaction --no-ansi --no-root

# Copy project files
COPY . .

# Create relevant data dirs
RUN mkdir -p data workspaces projects

# Start process
EXPOSE 8080
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8080"]
