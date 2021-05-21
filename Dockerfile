FROM python:3.8-slim

WORKDIR /opt/app/

# Copy files
COPY logger.py /opt/app
COPY requirements.txt /opt/app
COPY app.py /opt/app

# Install project python dependencies
RUN pip install -r requirements.txt

# Run app.py
CMD python app.py -c $CGX_TOKEN