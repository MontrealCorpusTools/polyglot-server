angular.module('pgdb.utterances')
    .directive('waveformPlot', function () {

        var margin = {top: 40, right: 30, bottom: 40, left: 90},
            height = 400;
        var width = 900;

        return {
            restrict: 'E',
            replace: true,
            template: '<div class="chart"></div>',

            controllerAs: 'ctrl',
            scope: {
                height: '=height',
                data: '=data',
                begin: '=',
                end: "=",
                hovered: '&hovered',
                seekFn: '&seekFn',
                selectEndUpdateFn: '&selectEndUpdateFn',
                playFn: '&playFn'
            },
            link: function (scope, element, attrs) {
                var vis = d3.select(element[0]);
                var x = d3.scaleLinear().range([0, width]).nice();

                var xt = x;
                var selection_begin, selection_end, selection_anchor;

                scope.$watch('begin', function (newVal, oldVal) {
                    if (!newVal) {
                        return;
                    }
                    x.domain([newVal, x.domain()[1]]);
                });

                scope.$watch('end', function (newVal, oldVal) {
                    if (!newVal) {
                        return;
                    }
                    x.domain([x.domain()[0], newVal]);
                });

                scope.$watch('data', function (newVal, oldVal) {
                    vis.selectAll('*').remove();
                    if (!newVal) {
                        return;
                    }

// Make x axis
                    var xaxis = d3.axisBottom(x)
                        .ticks(10);

                    var zoom_scales = [1, 30];
                    var waveform_y = d3.scaleLinear().range([height, 0]).nice();
                    waveform_y.domain(d3.extent(newVal, function (d) {
                        return d.y;
                    }));
                    var waveform_padding = (waveform_y.domain()[1] - waveform_y.domain()[0]) * 0.05;
                    waveform_y.domain([waveform_y.domain()[0] - waveform_padding, waveform_y.domain()[1] + waveform_padding]);

                    var waveform_valueline = d3.line()
                        .x(function (d) {
                            return x(d.x);
                        }).y(function (d) {
                            return waveform_y(d.y);
                        });

                    var waveform_x_function = function (d) {
                        return x(d.x);
                    };
                    var waveform_yaxis = d3.axisLeft(waveform_y)
                        .ticks(5);

                    var waveform_vis = vis
                        .append("svg")
                        .attr("width", width + margin.right + margin.left)
                        .attr("height", height + margin.top + margin.bottom)
                        .append("g")
                        .attr("transform", "translate(" + margin.left + "," + margin.top + ")");

// Draw the Plotting region------------------------------
// X axis lines (bottom and top).
                    waveform_vis.append("g")
                        .attr("class", "xaxis")
                        .attr("transform", "translate(0," + height + ")")
                        .call(xaxis);

                    waveform_vis.append("g")
                        .attr("class", "yaxis")
                        .call(waveform_yaxis);

                    waveform_vis.append("text")
                        .attr("x", 0 - height / 2)
                        .attr("y", -margin.left + 20)
                        .attr("transform", "rotate(-90)")
                        .style("text-anchor", "middle")
                        .style("font-size", "16px")
                        .text("Amplitude");


// End Draw the Plotting region------------------------------


                    waveform_vis.append("clipPath")
                        .attr("id", "waveform_clip")
                        .append("rect")
                        .attr("x", 0)
                        .attr("y", 0)
                        .attr("width", width)
                        .attr("height", height);

                    var waveform_viewplot = waveform_vis.append("g").attr("clip-path", "url(#waveform_clip)");

                    var waveform_playline_x = x(0);

                    var waveform_playline = waveform_viewplot.append('line').attr("class", "playline").style("stroke", "red")
                        .attr("x1", xt(0))
                        .attr("y1", 0)
                        .attr("x2", xt(0))
                        .attr("y2", height);

                    waveform_viewplot.append("path")
                        .attr("class", "line").data([newVal]).attr('d', function (d) {
                        return waveform_valueline(d);
                    })
                        .style('stroke', 'black');

                    var waveform_pane = waveform_vis.append("rect")
                        .attr("class", "pane")
                        .attr("width", width)
                        .attr("height", height);

                    var drag = d3.drag()
                        .on("start", function () {
                            waveform_viewplot.selectAll('rect.selection').remove();
                            var coords = d3.mouse(this);
                            selection_begin = xt.invert(coords[0]);
                            selection_anchor = selection_begin;
                            selection_end = null;
                            scope.$emit('UPDATESELECT', selection_end);
                            waveform_playline.attr("x1", xt(selection_begin))
                                .attr("x2", xt(selection_begin));
                            scope.$emit('SEEK', selection_begin);
                            waveform_viewplot.append("rect")
                                .attr('class', "selection")
                                .attr('x', coords[0])
                                .attr('y', 0)
                                .attr('width', 0)
                                .attr('height', height)
                                .attr('fill', 'red')
                                .attr('opacity', 0.3);
                        })
                        .on("drag", function () {
                            var p = d3.mouse(this);
                            var point_time = xt.invert(p[0]);

                            if (point_time < selection_anchor) {
                                selection_begin = point_time;
                                selection_end = selection_anchor;
                                selection_begin = point_time;
                            }
                            else {
                                selection_begin = selection_anchor;
                                selection_end = point_time;
                            }
                            waveform_viewplot.select("rect.selection").attr('x', xt(selection_begin)).attr('width', xt(selection_end) - xt(selection_begin));
                            waveform_playline.attr("x1", xt(selection_begin))
                                .attr("x2", xt(selection_begin));
                            scope.$emit('UPDATESELECT', selection_end);
                            scope.$emit('SEEK', selection_begin);


                        });

                    waveform_vis.call(d3.zoom()
                        .scaleExtent(zoom_scales)
                        .translateExtent([[0, 0], [width, height]])
                        .on("zoom", zoomed)
                        .on('end', zoomended))
                        .on("mousedown.zoom", null)
                        .on("touchstart.zoom", null)
                        .on("touchmove.zoom", null)
                        .on("touchend.zoom", null)
                        .on('click', function () {
                            if (d3.event.defaultPrevented) return; // click suppressed
                            var coords = d3.mouse(this);
                            var selection_begin = xt.invert(coords[0]);
                            scope.$emit('SEEK', selection_begin);
                            selection_end = null;
                            scope.$emit('UPDATESELECT', selection_end);
                            waveform_playline.attr("x1", xt(selection_begin))
                                .attr("x2", xt(selection_begin));

                        })
                        .call(drag);
                    scope.$on('UPDATEPLAY', function (e, time) {

                        waveform_playline.attr('x1', xt(time))
                            .attr('x2', xt(time));
                    });

                    function zoomFunc(transform) {
                        transform.x = Math.min(transform.x, 0);
                        xt = transform.rescaleX(x);
                        waveform_vis.select('.x.axis').call(xaxis.scale(xt));

                        waveform_valueline = d3.line()
                            .x(function (d) {
                                return xt(d.x);
                            })
                            .y(function (d) {
                                return waveform_y(d.y);
                            });
                        waveform_playline.attr("x1", xt(selection_begin))
                            .attr("x2", xt(selection_begin));
                        if (selection_end != null) {
                            waveform_viewplot.select("rect.selection").attr('x', xt(selection_begin)).attr('width', xt(selection_end) - xt(selection_begin));
                        }
                        drawWaveform();
                    }

                    function zoomed() {
                        scope.$emit('ZOOM_REQUESTED', d3.event.transform);
                    }

                    scope.$on('ZOOM', function (e, lastTransform) {
                        zoomFunc(lastTransform);
                    });

                    function zoomended() {
                        var e = d3.event.sourceEvent;
                        if (e != null && e.button == 0 && e.movementX < 10) {
                            var coords = d3.mouse(this);
                            selection_begin = xt.invert(coords[0]);
                            waveform_playline.attr("x1", xt(selection_begin))
                                .attr("x2", xt(selection_begin));
                        }
                    }

                    function drawWaveform() {
                        waveform_vis.select('.yaxis').call(waveform_yaxis);
                        waveform_vis.selectAll("path.line")
                            .attr('d', function (d) {
                                return waveform_valueline(d);
                            });
                    }
                });
            }
        }
    }).directive('spectrogramPlot', function () {

    var margin = {top: 40, right: 30, bottom: 40, left: 90},
        height = 400;
    var width = 900;
    return {
        restrict: 'E',
        replace: true,
        template: '<div class="chart"></div>',
        scope: {
            height: '=height',
            data: '=data',
            begin: '=',
            end: '=',
            hovered: '&hovered'
        },
        link: function (scope, element, attrs) {
            var vis = d3.select(element[0]);

            var x = d3.scaleLinear().range([0, width]).nice();

            scope.$watch('begin', function (newVal, oldVal) {
                if (!newVal) {
                    return;
                }
                x.domain([newVal, x.domain()[1]]);
            });

            scope.$watch('end', function (newVal, oldVal) {
                if (!newVal) {
                    return;
                }
                x.domain([x.domain()[0], newVal]);
            });
            scope.$watch('data', function (newVal, oldVal) {
                console.log(newVal);
                vis.selectAll("*").remove();
                //pitch_viewplot.append('g').selectAll("circle.original").remove();
                if (!newVal) {
                    return;
                }

                // Make x axis
                var xaxis = d3.axisBottom(x)
                    .ticks(10);

                var zoom_scales = [1, 30];
                var specgram_y = d3.scaleLinear().range([height, 0]),
                    specgram_z = d3.scaleLinear().range(["white", "black"]);

                specgram_y.domain(d3.extent(newVal.values, function (d) {
                    return d.frequency;
                }));
                specgram_y.domain([specgram_y.domain()[0], specgram_y.domain()[1] + newVal.freq_step]);
                specgram_z.domain(d3.extent(newVal.values, function (d) {
                    return d.power;
                }));


                var specgram_yaxis = d3.axisLeft(specgram_y)
                    .ticks(5);


                var specgram_svg = vis.attr('height', height + margin.top + margin.bottom).append('svg')
                    .attr('class', 'combined')
                    .attr("width", width + margin.left + margin.right)
                    .attr("height", height + margin.top + margin.bottom)
                    .append("g")
                    .attr("transform", "translate(" + margin.left + "," + margin.top + ")");

                var specgram_canvas = vis.append("canvas")
                    .attr('class', 'combined')
                    //.attr("x",  margin.left)
                    //.attr("y", margin.top)
                    .style("padding", margin.top + "px " + margin.right + "px " + margin.bottom + "px " + margin.left + "px ")
                    .attr("width", width + "px")
                    .attr("height", height + "px");


                specgram_svg.append("g")
                    .attr("class", "xaxis")
                    .attr("transform", "translate(0," + height + ")")
                    .call(xaxis)
                    .append("text")
                    .attr("class", "label")
                    .attr("x", width / 2)
                    .attr("y", margin.bottom - 10)
                    .style("text-anchor", "middle")
                    .text("Time (s)");

                specgram_svg.append("g")
                    .attr("class", "yaxis")
                    .call(specgram_yaxis)
                    .append("text")
                    .attr("class", "label")
                    .attr("x", 0 - height / 2)
                    .attr("y", -margin.left + 20)
                    .style("text-anchor", "middle")
                    .attr("transform", "rotate(-90)")
                    .style("font-size", "16px")
                    .text("Frequency (Hz)");


                var specgram_context = specgram_canvas.node().getContext("2d");

                var xGridSize = x(newVal.time_step) - x(0) + 2,
                    yGridSize = specgram_y(newVal.freq_step) - specgram_y(0) - 2;

                function zoomFunc(lastTransform) {

                    lastTransform.x = Math.min(lastTransform.x, 0);
                    xt = lastTransform.rescaleX(x);
                    specgram_svg.select('.x.axis').call(xaxis.scale(xt));

                    specgram_context.save();
                    specgram_context.clearRect(0, 0, width, height);
                    specgram_context.translate(lastTransform.x, 0);
                    specgram_context.scale(lastTransform.k, 1);
                    drawSpectrogram();
                    specgram_context.restore();
                }

                var zoomed = function () {
                    scope.$emit('ZOOM_REQUESTED', d3.event.transform);


                };
                scope.$on('ZOOM', function (e, res) {
                    zoomFunc(res);
                });
                specgram_canvas.call(d3.zoom()
                    .scaleExtent(zoom_scales)
                    .translateExtent([[0, 0], [width, height]])
                    .on("zoom", zoomed));

                function drawSpectrogram() {
                    specgram_svg.select('.yaxis').call(specgram_yaxis);
                    newVal.values.forEach(drawRect);
                }

                function drawRect(d) {
                    //Draw the rectangle

                    specgram_context.fillStyle = specgram_z(d.power);
                    specgram_context.fillRect(x(d.time), specgram_y(d.frequency), xGridSize + 2, yGridSize);
                }

                drawSpectrogram();

            });

        }
    }
}).directive('pitchPlot', function () {

    var margin = {top: 40, right: 30, bottom: 40, left: 90},
        height = 400;
    var width = 900;

    return {
        restrict: 'E',
        replace: true,
        templateUrl: '/static/pgdb/components/utterances/pitch_plot.html',
        scope: {
            height: '=height',
            data: '=data',
            begin: '=',
            end: '=',
            hovered: '&hovered'
        },
        link: function (scope, element, attrs) {
            var vis = d3.select(element[0]).select('.plot');

            var x = d3.scaleLinear().range([0, width]).nice();
            var xt = x;
            var selection_begin, selection_end, selection_anchor;

            scope.available_pitch_sources = ['praat', 'reaper'];
            scope.newPitchSettings = {};
            scope.newPitchSettings.source = 'praat';
            scope.newPitchSettings.min_pitch = 50;
            scope.newPitchSettings.max_pitch = 500;

            scope.$watch('begin', function (newVal, oldVal) {
                if (!newVal) {
                    return;
                }
                x.domain([newVal, x.domain()[1]]);
            });

            scope.$watch('end', function (newVal, oldVal) {
                if (!newVal) {
                    return;
                }
                x.domain([x.domain()[0], newVal]);
            });

            scope.$watch('data', function (newVal, oldVal) {
                console.log(newVal);
                vis.selectAll("*").remove();
                //pitch_viewplot.append('g').selectAll("circle.original").remove();
                if (!newVal) {
                    return;
                }

                scope.generateNewTrack = function () {
                    scope.$emit('TRACK_REQUESTED', scope.newPitchSettings);
                };

                scope.saveTrack = function () {
                    scope.$emit('SAVE_TRACK', newVal);
                };

                var div = d3.select("body").append("div")
                    .attr("class", "tooltip")
                    .style("opacity", 0);

                // Make x axis
                var xaxis = d3.axisBottom(x)
                    .ticks(10);

                var zoom_scales = [1, 30];

                var zoomFunc = function (lastTransform) {
                    lastTransform.x = Math.min(lastTransform.x, 0);
                    xt = lastTransform.rescaleX(x);
                    pitch_vis.select('.xaxis').call(xaxis.scale(xt));
                    pulse_x_function = function (d) {
                        return xt(d.x);
                    };
                    pitch_x_function = function (d) {
                        return xt(d.x);
                    };
                    pitch_valueline = pitch_valueline.x(pitch_x_function);
                    drawPitchTrack();
                };

                var zoomed = function () {
                    scope.$emit('ZOOM_REQUESTED', d3.event.transform);


                };
                scope.$on('ZOOM', function (e, res) {
                    zoomFunc(res);
                });
                var drawPitchTrack = function () {
                    pitch_vis.select('.yaxis').call(pitch_yaxis);
                    pitch_vis.selectAll("path.line")
                        .attr('d', function (d) {
                            return pitch_valueline(d);
                        });
                    pitch_vis.selectAll('circle')
                        .attr("cx", pitch_x_function)
                        .attr("cy", function (d) {
                            return pitch_y(d.y);
                        })
                        .on("mouseover", function (d) {
                            div.transition()
                                .duration(200)
                                .style("opacity", .9);
                            div.html(d.x + "<br/>" + d.y.toFixed(2))
                                .style("left", (d3.event.pageX) + "px")
                                .style("top", (d3.event.pageY - 28) + "px");
                        })
                        .on("mouseout", function (d) {
                            div.transition()
                                .duration(500)
                                .style("opacity", 0);
                        });
                };

                var pitch_y = d3.scaleLinear().range([height, 0]).nice();
                var pitch_padding = (pitch_y.domain()[1] - pitch_y.domain()[0]) * 0.05;
                pitch_y.domain([pitch_y.domain()[0] - pitch_padding, pitch_y.domain()[1] + pitch_padding]);

                var pitch_x_function = function (d) {
                    return x(d.x);
                };
                var pitch_valueline = d3.line()
                    .x(pitch_x_function).y(function (d) {
                        return pitch_y(d.y);
                    });

                var pitch_yaxis = d3.axisLeft(pitch_y)
                    .ticks(5);

                var pitch_vis = vis
                    .append("svg")
                    .attr("width", width + margin.right + margin.left)
                    .attr("height", height + margin.top + margin.bottom)
                    .append("g")
                    .attr("transform", "translate(" + margin.left + "," + margin.top + ")");

// Draw the Plotting region------------------------------
// X axis lines (bottom and top).
                pitch_vis.append("g")
                    .attr("class", "xaxis")
                    .attr("transform", "translate(0," + height + ")")
                    .call(xaxis);

                pitch_vis.append("g")
                    .attr("class", "yaxis")
                    .call(pitch_yaxis);

                pitch_vis.append("text")
                    .attr("x", 0 - height / 2)
                    .attr("y", -margin.left + 20)
                    .attr("transform", "rotate(-90)")
                    .style("text-anchor", "middle")
                    .style("font-size", "16px")
                    .text("F0");

                var pitch_clippath = pitch_vis.append("clipPath")
                    .attr("id", "pitch_clip")
                    .append("rect")
                    .attr("x", 0)
                    .attr("y", 0)
                    .attr("width", width)
                    .attr("height", height);

                var pitch_pane = pitch_vis.append("rect")
                    .attr("class", "pane")
                    .attr("width", width)
                    .attr("height", height);


                var drag = d3.drag()
                    .on("start", function () {
                        console.log('drag started!');
                        pitch_viewplot.selectAll('rect.selection').remove();
                        var coords = d3.mouse(this);
                        selection_begin = xt.invert(coords[0]);
                        selection_anchor = xt.invert(coords[0]);
                        selection_end = null;
                        pitch_viewplot.append("rect")
                            .attr('class', "selection")
                            .attr('x', coords[0])
                            .attr('y', 0)
                            .attr('width', 0)
                            .attr('height', height)
                            .attr('fill', 'red')
                            .attr('opacity', 0.3);
                    })
                    .on("drag", function () {
                        var p = d3.mouse(this);
                        var point_time = xt.invert(p[0]);
                        if (point_time < selection_anchor) {
                            selection_begin = point_time;
                            selection_end = selection_anchor;
                            selection_begin = point_time;
                        }
                        else {
                            selection_begin = selection_anchor;
                            selection_end = point_time;
                        }
                        pitch_viewplot.select("rect.selection").attr('x', xt(selection_begin)).attr('width', xt(selection_end) - xt(selection_begin));
                    }).on("end", function () {
                        if (!d3.event.ctrlKey) {
                            pitch_viewplot.selectAll('circle.selected').style("fill", 'blue').classed("selected", false);
                        }
                        pitch_viewplot.selectAll('circle').filter(function (d) {
                            return (d.x >= selection_begin && d.x <= selection_end)
                        }).style("fill", 'red').classed('selected', true);
                        pitch_viewplot.selectAll('rect.selection').remove();
                        selection_begin = null;
                        selection_end = null;
                        selection_anchor = null;
                    });


                pitch_pane.call(d3.zoom()
                    .scaleExtent(zoom_scales)
                    .translateExtent([[0, 0], [width, height]])
                    .on("zoom", zoomed))
                    .on("mousedown.zoom", null)
                    .on("touchstart.zoom", null)
                    .on("touchmove.zoom", null)
                    .on("touchend.zoom", null)
                    .on('click', function () {
                        return;
                        if (d3.event.defaultPrevented) return; // click suppressed
                        var coords = d3.mouse(this);
                        var selection_begin = xt.invert(coords[0]);
                        scope.$emit('SEEK', selection_begin);
                        selection_end = null;
                        scope.$emit('UPDATESELECT', selection_end);
                        waveform_playline.attr("x1", xt(selection_begin))
                            .attr("x2", xt(selection_begin));

                    })
                    .call(drag);

                var pitch_viewplot = pitch_vis.append("g").attr("clip-path", "url(#pitch_clip)");

                var line = pitch_viewplot.append('g');
                var circles = pitch_viewplot.append('g');
                console.log(newVal);

                function updateTrack() {
                    line.selectAll('path').remove();
                    line.append("path")
                        .attr("class", "line")
                        .classed("original", true).data([newVal]).attr('d', function (d) {
                        return pitch_valueline(d);
                    })
                        .style('stroke', 'blue');
                    circles.selectAll('circle').remove();
                    circles.selectAll('circle').data(newVal)
                        .enter().append("circle")
                        .classed("original", true)
                        .attr("r", 5)
                        .attr("cx", pitch_x_function)
                        .attr("cy", function (d) {
                            return pitch_y(d.y);
                        })
                        .style("fill", 'blue')
                        .on("click", function () {
                            if (!d3.event.ctrlKey) {
                                pitch_viewplot.selectAll('circle.selected').style("fill", 'blue').classed("selected", false);
                            }
                            d3.select(this).attr('class', 'selected').style("fill", "red");
                        });
                    pitch_y.domain(d3.extent(newVal, function (d) {
                        return d.y;
                    }));
                    pitch_padding = (pitch_y.domain()[1] - pitch_y.domain()[0]) * 0.05;
                    pitch_y.domain([pitch_y.domain()[0] - pitch_padding, pitch_y.domain()[1] + pitch_padding]);

                }

                updateTrack();

                scope.doubleSelected = function () {
                    pitch_viewplot.selectAll('circle.selected').data().forEach(function (d) {
                        d['y'] *= 2
                    });
                    pitch_viewplot.selectAll('circle.selected').style("fill", 'blue').classed("selected", false);

                    var new_domain = d3.extent(pitch_viewplot.selectAll('circle').data(), function (d) {
                        return d.y;
                    });

                    pitch_padding = (new_domain[1] - new_domain[0]) * 0.05;
                    pitch_y.domain([new_domain[0] - pitch_padding, new_domain[1] + pitch_padding]);
                    drawPitchTrack();
                };

                scope.halveSelected = function () {
                    pitch_viewplot.selectAll('circle.selected').data().forEach(function (d) {
                        d['y'] /= 2
                    });
                    pitch_viewplot.selectAll('circle.selected').style("fill", 'blue').classed("selected", false);

                    var new_domain = d3.extent(pitch_viewplot.selectAll('circle').data(), function (d) {
                        return d.y;
                    });

                    pitch_padding = (new_domain[1] - new_domain[0]) * 0.05;
                    pitch_y.domain([new_domain[0] - pitch_padding, new_domain[1] + pitch_padding]);
                    drawPitchTrack();
                };

                scope.smoothSelected = function () {
                    var all_data = pitch_viewplot.selectAll('circle').data();
                    pitch_viewplot.selectAll('circle.selected').data().forEach(function (d) {
                        var ind = all_data.findIndex(function (e) {
                            return e['x'] == d['x'];
                        });
                        if (ind != 0 && ind != all_data.length - 1) {
                            d['y'] = (all_data[ind + 1]['y'] - all_data[ind - 1]['y']) / 2 + all_data[ind - 1]['y']
                            console.log(d['y']);
                        }
                    });
                    //pitch_viewplot.selectAll('circle.selected').style("fill", 'blue').classed("selected",false);

                    var new_domain = d3.extent(pitch_viewplot.selectAll('circle').data(), function (d) {
                        return d.y;
                    });

                    pitch_padding = (new_domain[1] - new_domain[0]) * 0.05;
                    pitch_y.domain([new_domain[0] - pitch_padding, new_domain[1] + pitch_padding]);
                    drawPitchTrack();
                };

                scope.removeSelected = function () {
                    pitch_viewplot.selectAll('circle.selected').data().forEach(function (d) {
                        var ind = newVal.findIndex(function (e) {
                            return e['x'] == d['x'];
                        });
                        newVal.splice(ind, 1);
                    });
                    updateTrack();
                    pitch_viewplot.selectAll('circle.selected').style("fill", 'blue').classed("selected", false);
                };

                drawPitchTrack();

            });

            scope.$watch('height', function (d, i) {

            })
        }
    }
}).directive('bestiaryPlot', function () {

    var margin = {top: 40, right: 30, bottom: 40, left: 90},
        height = 400;
    var width = 900;
    return {
        restrict: 'E',
        replace: true,
        template: '<div class="chart"></div>',
        scope: {
            height: '=height',
            data: '=data',
            hovered: '&hovered'
        },
        link: function (scope, element, attrs) {
            var vis = d3.select(element[0]);
            var x = d3.scaleLinear().range([0, width]).nice();
            // Adjusted Close
            var y = d3.scaleLinear().range([height, 0]).nice();

            // Make x axis
            var xaxis = d3.axisBottom()
                .scale(x)
                .ticks(10);

            // Make y axis
            var yaxis = d3.axisLeft()
                .scale(y)
                .ticks(5);

            var valueline = d3.line().defined(function (d) {
                return d.y != null;
            })
                .x(function (d) {
                    return x(d.x);
                })
                .y(function (d) {
                    return y(d.y);
                });
            scope.$watch('data', function (newVal, oldVal) {
                console.log(newVal);
            });

        }
    }
});