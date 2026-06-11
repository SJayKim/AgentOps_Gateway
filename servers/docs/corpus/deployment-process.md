# Deployment Process

Deployments run through the CI pipeline on every merge to main. Images are
built with Docker, tagged with the commit hash, and pushed to the registry.
Production deploys require a green canary stage and approval from a release
manager. Hotfixes follow the same pipeline with an expedited review.
