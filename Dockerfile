FROM mcr.microsoft.com/windows/servercore:ltsc2025

WORKDIR /app

RUN powershell -Command \
    Set-ExecutionPolicy Bypass -Scope Process -Force; \
    [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072; \
    iex ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))

RUN choco install firefox -y

RUN choco install python -y

RUN powershell -Command \
    $env:PATH = [System.Environment]::GetEnvironmentVariable('PATH', 'Machine') + ';' + [System.Environment]::GetEnvironmentVariable('PATH', 'User'); \
    [System.Environment]::SetEnvironmentVariable('PATH', $env:PATH, 'Process')

COPY requirements.txt /app/requirements.txt

RUN pip install -r requirements.txt

COPY . /app/

EXPOSE 5000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "5000"]