# Heroku deployment

```bash
docker build -t osmaps .
heroku container:push web
```

The OS_KEY and OS_URL must be defined in the docker
execution environment.
