#!/usr/bin/env bash
set -euo pipefail

# Create host-side mount directories if they don't exist
mkdir -p out_tmp_dirs patche_dirs workspace

echo "Building Defects4C Docker image..."
docker build -t defects4c:v2 .

echo ""
echo "Done. Next steps:"
echo ""
echo "  1. Clone repos on HOST (large disk):"
echo "     cd defectsc_tpl && bash bulk_git_clone_v2.sh mini"
echo ""
echo "  2. Start the service:"
echo "     docker-compose up -d"
echo ""
echo "  3. Test:"
echo "     curl http://localhost:11111/health"
echo "     python3 http_tutorial.py --cppcheck-only"
