var chartsData = {};

function dpidToName(sdpid) {
    var dpid = parseInt(sdpid);

    var layer = (dpid >> 16) & 0xff;
    var pod = (dpid >> 8) & 0xff;
    var sw = dpid & 0xff;

    switch (layer) {
        case 0:
            return 'Core Switch ' + (sw-1);
        case 1:
            return 'Aggregation Switch, pod: ' + pod + ' switch: ' + sw;
        case 2:
            return 'Edge Switch, pod: ' + pod + ' switch: ' + sw;
        case 3:
            return 'Internet'
        default:
            return null;
    }
}

function startWebSocket() {
    var ws = new WebSocket("ws://" + window.location.host + "/api/ws");

    ws.onmessage = function(e) {
        var data = JSON.parse(e.data);

        //'graph-' + dpid + '-' + portId
        _.forEach(data.data, function(portstat, portId) {
            var id = 'graph-' + data.dpid + '-' + portId;
            var chartData = chartsData[id];

            rx_bytes = portstat.rx_bytes - chartData.lastpoint.rx_bytes;
            tx_bytes = portstat.tx_bytes - chartData.lastpoint.tx_bytes;
            delta = data.time - chartData.labels[chartData.labels.length-1];


            chartData.labels.push(data.time);
            if (chartData.labels.length > 100) {
                chartData.labels.shift();
            }

            chartData.rx_data.push(rx_bytes/delta);
            if (chartData.rx_data.length > 100) {
                chartData.rx_data.shift();
            }

            chartData.tx_data.push(tx_bytes/delta);
            if (chartData.tx_data.length > 100) {
                chartData.tx_data.shift();
            }

            chartData.lastpoint = portstat;
            chartData.chart.update();
        })
    };
}

$('#middleboxForm').submit(function(e) {
    var form = $(this);

    $.post('/api/middlebox', form.serialize()).done(function(data) {
    });

    e.preventDefault();
});

$.ajax('/api/stats').done(function(data) {
    var graphs = $('#graphs');
    var options = $('#dpid_select');

    _.forEach(data, function(switchStats, dpid) {
        // Each switch need it's own section
        var switchContainer = $('<div></div>')
            .attr('id', 'graph-' + dpid)
            .addClass('switch-container')
            .appendTo(graphs);

        //
        $('<h2></h2>')
            .text(dpidToName(dpid))
            .appendTo(switchContainer);

        //
        options.append($('<option />').val(dpid).text(dpidToName(dpid)));

        _.forEach(switchStats.data, function(portStats, portId) {
            // Each switch has multiple ports
            var id = 'graph-' + dpid + '-' + portId;
            var portContainer = $('<div></div>')
                .attr('id', id)
                .addClass('port-container')
                .appendTo(switchContainer);

            //
            $('<h3></h3>')
                .text('Port ' + portId)
                .appendTo(portContainer)

            //
            var canvas = $('<canvas></canvas>')
                .appendTo(portContainer);

            //
            chartsData[id] = {
                labels: _.slice(switchStats.time, 1),
                rx_data: _.transform(portStats, function(result, stat, i) {
                    if (i > 1) {
                        bytes = stat.rx_bytes - portStats[i-1].rx_bytes
                        delta = switchStats.time[i] - switchStats.time[i - 1]
                        result.push(bytes/delta);
                    }
                }, []),
                tx_data: _.transform(portStats, function(result, stat, i) {
                    if (i > 1) {
                        bytes = stat.tx_bytes - portStats[i-1].tx_bytes
                        delta = switchStats.time[i] - switchStats.time[i - 1]
                        result.push(bytes/delta);

                    }
                }, []),
                lastpoint: portStats[portStats.length-1]
            };

            var ctx = canvas.get(0).getContext('2d');
            var chart = new Chart.Line(ctx, {
                data: {
                    labels: chartsData[id].labels,
                    datasets: [{
                        label: 'rx',
                        fill: false,
                        borderColor: "rgba(75,192,130,1)",
                        data: chartsData[id].rx_data
                    }, {
                        label: 'tx',
                        fill: false,
                        borderColor: "rgba(75,104,192,1)",
                        data: chartsData[id].tx_data
                    }]
                },
                options: {
                    responsive: false,
                    animation: {
                        duration: 0
                    },
                    legend: {
                        display: false
                    },
                    scales: {
                        yAxes: [{
                            ticks: {
                                min: 0
                                //max: 1000000000/8
                            }
                        }]
                    }
                }
            });

            chartsData[id].chart = chart;
        });
    });

    startWebSocket();
});
