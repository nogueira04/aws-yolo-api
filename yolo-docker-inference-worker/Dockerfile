FROM ultralytics/ultralytics:latest-cpu

ENV LAMBDA_TASK_ROOT=/var/task
WORKDIR ${LAMBDA_TASK_ROOT}
COPY app.py ./
COPY yolov8n.pt ./
COPY requirements.txt ./

RUN pip install --no-cache-dir -r requirements.txt

ENV PATH="/var/task:${PATH}" 
ENTRYPOINT [ "/usr/local/bin/python", "-m", "awslambdaric" ]

CMD [ "app.handler" ]
