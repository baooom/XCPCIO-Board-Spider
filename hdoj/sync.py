#! /usr/bin/env python3

import os
import json
import requests
import dateutil.parser
import yaml
from optparse import OptionParser
import logging
from time import strftime, localtime, time, sleep
from os import path, makedirs
from shutil import rmtree


def ensure_dir(s):
    if not path.exists(s):
        makedirs(s)


def ensure_no_dir(s):
    if path.exists(s):
        rmtree(s)


def json_output(data):
    return json.dumps(data, sort_keys=False, separators=(',', ':'), ensure_ascii=False)


def output(filename, data):
    with open(os.path.join(output_path, filename), 'w') as f:
        f.write(json_output(data))


# DEFAULT VALUE
__LOG_DIR__ = "./log"
output_path = "./"
fetch_sleep_time = 20

INF = 0x3f3f3f3f


def parse_options():
    global config_path, username, password, enableFileLog
    enableFileLog = False

    parser = OptionParser()

    parser.add_option(
        '-c', '--config_path',
        dest='config_path',
        type=str,
        help="Configuration files path"
    )

    parser.add_option(
        '-u', '--username',
        dest='username',
        type=str,
        help="username"
    )

    parser.add_option(
        '-p', '--password',
        dest='password',
        type=str,
        help="password"
    )

    parser.add_option(
        '-l', '--log',
        dest='log',
        type=int,
        help="enable log"
    )

    opts, args = parser.parse_args()

    if opts.config_path:
        config_path = opts.config_path
    else:
        config_path = "./sync.yaml"

    if opts.username:
        username = opts.username

    if opts.password:
        password = opts.password

    if opts.log:
        enableFileLog = True


def init_logging():
    global logger

    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        '%(asctime)s - %(filename)s[line:%(lineno)d] - %(levelname)s: %(message)s')

    if enableFileLog == True:
        ensure_dir(__LOG_DIR__)
        logFileName = '{}/{}.log'.format(__LOG_DIR__,
                                         strftime('%Y-%m-%dT%H:%M:%S', localtime(time())))
        fileHandler = logging.FileHandler(logFileName)
        fileHandler.setFormatter(formatter)
        logger.addHandler(fileHandler)

    consoleHandler = logging.StreamHandler()
    consoleHandler.setFormatter(formatter)
    logger.addHandler(consoleHandler)


def load_yaml(path):
    with open(path, 'r', encoding="utf-8") as f:
        data = yaml.load(f)
        return data


def parse_configuration():
    global config_yaml, username, password, contest_id, start_time, output_path, fetch_sleep_time
    config_yaml = load_yaml(config_path)

    logger.info(config_yaml)

    if "username" in config_yaml.keys():
        username = config_yaml["username"]

    if "password" in config_yaml.keys():
        password = config_yaml["password"]

    if "fetch_sleep_time" in config_yaml.keys():
        fetch_sleep_time = config_yaml["fetch_sleep_time"]

    contest_id = str(config_yaml["contest_id"])
    start_time = dateutil.parser.parse(config_yaml["start_time"])
    output_path = str(config_yaml["output_path"])

    ensure_dir(output_path)


def login():
    http = requests.Session()
    r = http.get(
        'http://acm.hdu.edu.cn/contests/contest_show.php?cid=' + contest_id)

    head = {'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'Connection': 'close', 'Referer': 'http://acm.hdu.edu.cn/userloginex.php?cid=' + contest_id}
    form = 'login=Sign%20In&username=' + username + '&userpass=' + password
    r = http.post('http://acm.hdu.edu.cn/userloginex.php?action=login&cid=' +
                  contest_id + '&notice=0', data=form, headers=head)

    return http


def my_parse_team(teamname):
    # should return id, "team name"
    # team137   普通高校 <br> TEAM NAME <br> SCHOOL

    secs = teamname.split('<br>')
    teamidd = secs[0].strip().split(' ')[0]

    if not teamidd.startswith('team'):
        return -1, '', '', False, False

    teamid = int(teamidd.replace('team', '')) - 1
    school = secs[2].strip()
    name = secs[1].strip()
    isStar = False if secs[0].find('打星') < 0 else True
    isGirls = False if secs[0].find('女队') < 0 else True

    return teamid, school, name, isStar, isGirls


def parse_team(line):
    return my_parse_team(line.split('"')[1])


def parse_teams(content):
    teams = content.split('pr(')
    teams = teams[2:]

    outputTeams = {}
    for t in teams:
        teamid, school, name, isStar, isGirls = parse_team(t)
        if teamid >= 0:

            outputTeams[teamid] = {
                "team_id": teamid,
                "name": name,
                "organization": school,
            }

            if isStar == True:
                outputTeams[teamid]['unofficial'] = 1
            else:
                outputTeams[teamid]['official'] = 1

            if isGirls == True:
                outputTeams[teamid]['girl'] = 1

    output("team.json", outputTeams)


def parse_probs(content):
    l = content.split('<td><a href="/contests/contest_showproblem.php?')[1:]
    return ['@p ' + chr(ord('A')+k) + ',TITLE,20,0' for k in range(len(l))]


def parse_verdict(content):
    # 暂时这么处理，之后 board 加了多状态后再补上
    if content.find("Accepted") >= 0:
        return "correct"
    else:
        return "incorrect"

    if content.find("Accepted") >= 0:
        return "OK"
    elif content.find("Wrong Answer") >= 0:
        return "WA"
    elif content.find("Time Limit Exceeded") >= 0:
        return "TL"
    elif content.find("Memory Limit Exceeded") >= 0:
        return "ML"
    elif content.find("Output Limit Exceeded") >= 0:
        return "IL"
    elif content.find("Presentation Error") >= 0:
        return "PE"
    elif content.find("Runtime Error") >= 0:
        return "RT"
    elif content.find("Compilation Error") >= 0:
        return "CE"
    else:
        return "RJ"


min_unsolved = 1
run_res = dict()


def parse_runs(http):
    global min_unsolved, run_res
    page = 1
    fetch_over = False

    while not fetch_over:
        logger.info('fetching status page ' + str(page))
        page_content = http.get('http://acm.hdu.edu.cn/contests/contest_status.php?cid=' +
                                contest_id + '&pid=&user=&lang=&status=&page=' + str(page)).text
        page_content = page_content.split(
            '<div align="center" class="FOOTER_LINK">')[0]
        items = page_content.split('<td height=22>')[1:]

        if len(items) == 0:
            break

        for item in items:
            cols = item.split('</td><td')
            stat = dict()
            stat['id'] = int(cols[0])
            if stat['id'] <= min_unsolved:
                fetch_over = True
            stat['status'] = parse_verdict(cols[2])
            stat['problem_id'] = int(cols[3].split(
                '&pid=')[1].split('" title=')[0]) - 1001
            stat['timestamp'] = (dateutil.parser.parse(
                cols[1].split('>')[1]) - start_time).seconds
            stat['team_id'] = int(cols[7].split('team')[1][0:3])
            run_res[stat['id']] = stat

        page = page + 1

    max_id = 0
    min_pd = INF
    outputRuns = []

    for runid in run_res:
        run = run_res[runid]
        if run['id'] > max_id:
            max_id = run['id']
        if run['status'] == 'pending' and run['id'] < min_pd:
            min_pd = run['id']
        outputRuns.append(run)

    min_unsolved = max_id + 1 if min_pd == INF else min_pd
    output("run.json", outputRuns)


def main():
    parse_options()
    init_logging()
    parse_configuration()

    http = login()

    while True:
        logger.info("fetching...")

        try:
            standings = http.get(
                'http://acm.hdu.edu.cn/contests/client_ranklist.php?cid=' + contest_id).text
            parse_teams(standings)
            parse_runs(http)
            logger.info("fetch successfully")
        except Exception as e:
            logger.error(e)

        logger.info("sleeping...")
        sleep(fetch_sleep_time)


if __name__ == '__main__':
    main()
