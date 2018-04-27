#!/usr/bin/env php

<?php

//if ($argc != 3) {
//    echo "Provide both host and port of CrateDB";
//    exit(1);
//}
//$host = $argv[1];
//$port = $argv[2];

$host = "localhost";
$port = "5432";

//
//if(empty($host) || empty($port)) {
//    echo "Provide both host and port of CrateDB";
//    exit(1);
//}

$pdo = new PDO('pgsql:dbname=doc;user=crate;host='.$host.';port='.$port);
$pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);

//create
$stmt = $pdo->query("create table pdo_test (x int, y string)");

//insert
$stmt = $pdo->prepare("insert into pdo_test (x, y) values (?, ?)");
$stmt->execute([1, "Postgres"]);

//select
$select = "select x, y from pdo_test";
foreach ($pdo->query($select) as $row) {
    print $row['x']."\n";
    print $row['y']."\n";
}

//delete
$stmt = $pdo->query("delete from pdo_test where x = 1");



//drop
$stmt = $pdo->query("drop table pdo_test");

?>
