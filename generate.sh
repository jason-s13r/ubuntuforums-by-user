echo "Generating index for user: '$1'"

uv run python ./user_index.py --full-log posted-by.log --archive-path ../Ubuntu-Forums-Archive --user "$1" --skip-existing

git add .
git commit -m "add listing for user: ${1}"

echo "$1" >> DONE
#git push

##sleep 1
