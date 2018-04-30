#!/usr/bin/env php

<?php

//if ($argc != 3) {
//    echo "Provide both host and port of CrateDB";
//    exit(1);
//}
//$host = $argv[1];
//$port = $argv[2];
//
//
//if(empty($host) || empty($port)) {
//    echo "Provide both host and port of CrateDB";
//    exit(1);
//}

$host = 'localhost';
$port = 5432;

//BASIC TEST
$pdo = new PDO('pgsql:dbname=doc;user=crate;host='.$host.';port='.$port);
$pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);

$stmt = $pdo->query("select name from sys.cluster");
while ($row = $stmt->fetch()) {
    echo "Cluster name: ".$row['name']."\n";
}

$create = $pdo->prepare("create table pdo_test (x int, y string)");
$create->execute();

$insert = $pdo->prepare("insert into pdo_test (x, y) values (?, ?)");
$insert->execute([1, "Postgres"]);

$pdo->prepare("refresh table pdo_test")->execute();
$stmt = NULL;

$select = $pdo->query("select x, y from pdo_test");
while ($row = $select->fetch()) {
    echo "x row: ".$row['x']."\n";
    echo "y row: ".$row['y']."\n";
}

$delete = $pdo->prepare("delete from pdo_test where x = 1");
$delete->execute();

$stmt = $pdo->query("drop table pdo_test");

//SSL TEST
//phpinfo();
$pdo = NULL;
try {
    $pdo = new PDO('pgsql:dbname=doc;user=crate;host=' . $host . ';port=' . $port);
    $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
}catch (PDOException $e) {
    print $e->getMessage();
}
?>
