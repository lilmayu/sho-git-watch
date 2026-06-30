# sho-git-watch

Shamelessly stolen from https://github.com/Vocaned/github-webhook-poller. Thank you for the original code!

## Prerequisites

- Python 3.11 or higher
- A GitHub Personal Access Token, create one [here](https://github.com/settings/personal-access-tokens). You don't have to give it any permissions.
- A Discord Webhook URL

## Usage

```
git clone https://github.com/lilmayu/sho-git-watch.git
cd sho-git-watch
pip install -r requirements.txt
cp config.example.toml config.toml

echo !!! Edit the config in config.toml !!!
echo !!! after that, run using 'python -m src'
```

### when running with venv (unix-like)

```
git clone https://github.com/lilmayu/sho-git-watch.git
cd sho-git-watch
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.example.toml config.toml

echo !!! Edit the config in config.toml !!!
echo !!! after that, run using 'python -m src'
```

### when running with venv (Windows)

```
git clone https://github.com/lilmayu/sho-git-watch.git
cd sho-git-watch
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy config.example.toml config.toml

echo !!! Edit the config in config.toml !!!
echo !!! after that, run using 'python -m src'
```

## Configuration

See [config.example.toml](./config.example.toml)

## Help / Support

Go outside and pray for me to appear. After not being able to summon me, send me a message on discord (`mayuna_`).