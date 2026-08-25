@languages ||= []
@languages +=
[
  {
    id: 71,
    name: "Python (3.8.1)",
    is_archived: false,
    source_file: "script.py",
    run_cmd: "/usr/local/python-3.8.1/bin/python3 script.py"
  },
  {
    id: 82,
    name: "SQL (SQLite 3.27.2)",
    is_archived: false,
    source_file: "script.sql",
    run_cmd: "/bin/cat script.sql | /usr/bin/sqlite3 db.sqlite"
  }
]