# Use Python 3.9 as base image
FROM python:3.9-slim

# Set working directory
WORKDIR /app

# Create data directory for mounting
RUN mkdir -p /app/data && mkdir -p /app/stopwords

# Copy requirements file
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY api.py md_storage.py service_registration.py service.json /app/
COPY stopwords/* /app/stopwords/
COPY help /app/help/

# Expose the port the app runs on
EXPOSE 9008

# Command to run the application
CMD ["python", "/app/api.py"] 