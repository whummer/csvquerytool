import cmd
import csv
import itertools
import logging
import os
import sqlite3
import sys

_logger = logging.getLogger(__file__)

AUTO_RENAME_DUPLICATE_COLUMN_NAMES = True
DEFAULT_ENCODING = 'UTF-8'
GUESS_TYPE_FROM_N_ROWS = 10000
ROW_PADDING_STRING = '' # if a row is truncated, missing cells will be filled in with this string

# TODO: sqlite only natively supports the types TEXT, INTEGER, FLOAT, BLOB and NULL.
# Support for extra types, such as datetimes, can be added with the detect_types
# parameter and by registering custom converters with register_converter().
def stripped_string(s):
    return s.strip('%$?').replace(',', '')

CAST_FUNCS = [
    (lambda s: int(stripped_string(s)) if stripped_string(s) != '' else None, 'INTEGER'),
    (lambda s: float(stripped_string(s)) if stripped_string(s) != '' else None, 'FLOAT'),
    (lambda s: s.decode(DEFAULT_ENCODING), 'TEXT'),
    (lambda s: bytes(s, DEFAULT_ENCODING), 'BLOB')
]

FORMAT_FUNCS = {
    int: lambda x: "%d" % x,
    float: lambda x: ("%f" % x).rstrip('0').rstrip('.'),
}

def sqlite_dict_factory(cursor, row):
    d = {}
    for i, col in enumerate(cursor.description):
        d[col[0]] = row[i]
    return d

def guess_type(example_data):
    for cast_func, cast_type in CAST_FUNCS:
        try:
            list(map(cast_func, example_data))
        except:
            continue
        else:
            return (cast_func, cast_type)
    raise ValueError("could not guess data type from example data: %r" % example_data)

def rename_duplicates(header):
    for col_num in range(len(header)):
        col_name = header[col_num]
        for n in itertools.count(2):
            if col_name not in header[:col_num]:
                break
            col_name = "%s%d" % (header[col_num], n)
        header[col_num] = col_name
    return header

def create_table(csv_file, db_cursor, table_name='csv', pad_rows=True):

    _logger.info("creating table '%s' from csv file: %s", table_name, csv_file)

    with open(csv_file) as csv_fh:
        reader = csv.reader(csv_fh)
        header = [col.strip() for col in next(reader)]
        if AUTO_RENAME_DUPLICATE_COLUMN_NAMES:
            header = rename_duplicates(header)
        elif len(header) != len(set(header)):
            raise ValueError("CSV file contains duplicate column names")

        # guess the types of each column (by sniffing the first GUESS_TYPE_FROM_N_ROWS rows)
        detect_type_rows = list(itertools.islice(reader, GUESS_TYPE_FROM_N_ROWS))
        guessed_type = dict()
        for col_num, col_name in enumerate(header):
            if pad_rows:
                example_data = [row[col_num].strip() if len(row) > col_num else ROW_PADDING_STRING for row in detect_type_rows]
            else:
                try:
                    example_data = [row[col_num].strip() for row in detect_type_rows]
                except IndexError:
                    raise ValueError('header and data row have different number of columns')
            cast_func, cast_type = guess_type(example_data)
            guessed_type[col_name] = cast_func
        _logger.info("guessed row types: %r", dict((k, dict(CAST_FUNCS)[v]) for k, v in guessed_type.items()))

        # create the sqlite table
        query_parts = list()
        for col_name in header:
            sqlite_type = dict(CAST_FUNCS)[guessed_type[col_name]]
            query_parts.append('"%s" %s' % (col_name, sqlite_type))
        sql = 'CREATE TABLE ' + table_name + ' (' + ', '.join(query_parts) + ')'
        db_cursor.execute(sql)

        # TODO: could do syntax & semantic checking of the SQL query here with an EXPLAIN
        # this would mean an error could be returned quicker, rather than waiting for the data to load
        # see http://stackoverflow.com/questions/2923832/how-do-i-check-sqlite3-syntax

        # insert the data into the table
        file_size = os.path.getsize(csv_file)
        num_rows = 0
        for num_rows, row in enumerate(itertools.chain(detect_type_rows, reader)):
            if pad_rows:
                padding = [ROW_PADDING_STRING,] * max(0, len(header) - len(row))
                row += padding
            elif len(row) != len(header):
                raise ValueError('header and data row have different number of columns')
            sql = "INSERT INTO " + table_name + " VALUES (" + ','.join('?' for _ in row) + ")"
            try:
                data = [guessed_type[col_name](val.strip()) for col_name, val in zip(header, row)]
            except ValueError as ex:
                if hasattr(ex, 'encoding'):
                    raise ValueError("not a valid '%s' sequence: %r" % (ex.encoding, ex.object))
                else:
                    raise ValueError("failed to convert row to guessed type, try increasing GUESS_TYPE_FROM_N_ROWS to improve guesses: %s" % ex)
            # TODO: this could probably be sped up with db_cursor.executemany()
            try:
                db_cursor.execute(sql, data)
            except:
                raise
            if num_rows > 0 and num_rows % 100000 == 0:
                _logger.info("loaded %.2f%% of csv file", 100.0 * csv_fh.tell() / file_size)
        _logger.info("inserted %d rows", num_rows)

def format_row(row, encoding=DEFAULT_ENCODING):
    """
    Convert a list of mixed elements to a list of strings, formatting integers and floats to remove exponent format.
    """
    row_formatted = list()
    for cell in row:
        if isinstance(cell, int):
            row_formatted.append("%d" % cell)
        elif isinstance(cell, float):
            row_formatted.append("%f" % cell)
        else:
            row_formatted.append(unicode(cell))
    return [cell.encode(encoding) if hasattr(cell, 'encode') else cell for cell in row_formatted]

def choose_table_names(csv_files, based_on_filename=True):
    """
    Function that chooses unique table names for CSV files that are going to be imported. The table names
    are based on the CSV file names if based_on_filename = True, otherwise they are just named "csv", "csv2",
    "csv3", etc.

    TODO: this function should also ensure they are valid SQL table names

    >>> choose_table_names(['/some/path/foo.csv', '/another/path/bar.csv'], based_on_filename=False)
    ['csv', 'csv2']
    >>> choose_table_names(['/some/path/foo.csv', '/another/path/bar.csv'], based_on_filename=True)
    ['foo', 'bar']
    >>> choose_table_names(['/some/path/foobar.csv', '/another/path/foobar.csv'], based_on_filename=True)
    ['foobar', 'foobar2']
    """
    table_names = list()
    for csv_file in csv_files:
        if based_on_filename:
            table_base_name = os.path.splitext(os.path.basename(csv_file))[0]
        else:
            table_base_name = 'csv'
        for n in itertools.count():
            table_name = '%s%d' % (table_base_name, n + 1) if n > 0 else table_base_name
            if table_name not in table_names:
                break
        table_names.append(table_name)
    return table_names

def run_query(query, csv_files, output_fh=sys.stdout):

    db_conn = sqlite3.connect(':memory:')
    db_cur = db_conn.cursor()
    table_names = choose_table_names(csv_files, based_on_filename=True)
    for csv_file, table_name in zip(csv_files, table_names):
        create_table(csv_file, db_cur, table_name)
    db_cur.execute(query)
    header = [col[0] for col in db_cur.description]
    writer = csv.writer(output_fh)
    writer.writerow(header)
    for row in db_cur:
        writer.writerow(format_row(row))

class SQLConsole(cmd.Cmd):

    prompt = "=> "

    def __init__(self, db_cur, *args, **kwargs):
        self.db_cur = db_cur
        self._stop = False
        cmd.Cmd.__init__(self, *args, **kwargs)

    def default(self, query):
        if query.endswith('EOF'):
            self._stop = True
            return
        try:
            self.db_cur.execute(query)
        except sqlite3.OperationalError as e:
            print >> sys.stderr, e
            return
        header = [col[0] for col in self.db_cur.description]
        writer = csv.writer(sys.stdout)
        writer.writerow(header)
        for row in self.db_cur:
            writer.writerow(format_row(row))

    def emptyline(self):
        self._stop = True

    def postcmd(self, stop, line):
        return self._stop

    def postloop(self):
        print

def interactive_console(csv_files):

    db_conn = sqlite3.connect(':memory:')
    db_cur = db_conn.cursor()
    table_names = choose_table_names(csv_files, based_on_filename=True)
    for csv_file, table_name in zip(csv_files, table_names):
        create_table(csv_file, db_cur, table_name)
        print("* file '%s' loaded into table '%s'" % (csv_file, table_name))
    console = SQLConsole(db_cur)
    console.cmdloop("SQL Interactive Console")
