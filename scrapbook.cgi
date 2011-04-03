#!/usr/bin/perl

# $Id: scrapbook.cgi,v 1.1 2010-03-27 01:33:21 ericblue76 Exp $
#
# Author: Eric Blue - http://eric-blue.com
# Project: Scrapbook
# Description:  Web Interface for the Firefox ScrapBook Extension (http://amb.vis.ne.jp/mozilla/scrapbook/)
#               Parse ScrapBook RDF and generate JSON for Simile Exhibit and HTML for JQuery File Tree
#
# Revision History:
#
# $Log: scrapbook.cgi,v $
# Revision 1.1  2010-03-27 01:33:21  ericblue76
# Initial checkin
#
# Revision 1.0 2009/12/27 15:17:00 ericblue
# Initial revision
#
#
#

use CGI;
use JSON;
use Cache::Memcached;
use LWP::Simple;
use LWP::UserAgent;
use MIME::Base64;
use DateTime;
use DateTime::Format::HTTP; 
use RDF::Simple::Parser;
use Data::Dumper;
use Log::Log4perl qw(:easy);
use CGI::Carp qw(fatalsToBrowser set_message warningsToBrowser);

use strict;
use vars qw($logger %scraps %index);

# URL for scrapbook.rdf - file or http format
my $scrapbook_url = "file:///home/ericblue76/Scrapbook/scrapbook.rdf";
# Hash for storing scrapbook items
my %scraps = {};
# Hash for storing and sorting titles for each folder
my %index  = {};
my $q      = new CGI;
warningsToBrowser(1);

# Init logger
Log::Log4perl->init("conf/logger.conf");
$logger = get_logger();

# Recursive function to print all directories and items in a hierarchy
sub print_tree_text {
    my ( $indent, $name ) = @_;

    my @folders = sort( get_folders_by_parent($name) );
    foreach (@folders) {
        print " " for ( 0 .. $indent );
        print "Folder = $_\n";
        foreach ( sort( get_items_by_folder($_) ) ) {
            print " " for ( 0 .. $indent + 1 );
            print "$_->{'title'}\n";
        }
        print_tree_text( $indent + 4, $_ );
    }

}

# Retrives all items from %scraps, sorts by title and adds to a new array
sub get_items_by_folder {
    my ($name) = @_;

    my @titles;
    my @found;

    foreach ( values %scraps ) {
        if (    ( $_->{'type'} eq "page" )
            and ( $_->{'parent_folder_name'} eq $name ) )
        {
            push( @titles, lc( $_->{'title'} ) );
        }
    }

    # FIXME: Figure out why some entries are being doubles (has to do with same name?).  Keep track with %added for now
    my %added;
    foreach my $title ( sort(@titles) ) {
        foreach ( values %scraps ) {
            if ( lc( $_->{'title'} ) eq $title ) {
                if (!defined($added{$_->{'id'}})) {
                    push( @found, $_ );
                    $added{$_->{'id'}} = "1";
                }
                  
            }
        }

    }

    return (@found);

}

# Looks up folders based on the parent folder name and returns a sorted array
sub get_folders_by_parent {
    my ($name) = @_;

    my @found;

    foreach ( values %index ) {
        if ( $_->{'parent_folder_name'} eq $name ) {
            push( @found, $_->{'title'} );
        }
    }

    return (sort(@found));
    
}

# Parses the scrapbook.rdf file and populates the %scraps hash
sub parse_rdf {

    # Base URL to use when providing links to saved Scrapbook URLs
    my $base_url = "http://mywebserver/scrapbook";

    $logger->info("Fetching scrapbook.rdf from $scrapbook_url");
    my $rdf = LWP::Simple::get($scrapbook_url);
    $logger->info( "Got RDF, size = ", length($rdf) );
    my $parser = RDF::Simple::Parser->new( base => $scrapbook_url );
    $logger->info("Parsing RDF");
    my @triples = $parser->parse_rdf($rdf);

    $logger->info("Performing initial parse and populating %scraps");
    foreach (@triples) {
        my $urn = $_->[0];

        # Item format - e.g. 'urn:scrapbook:item20060811120125'
        my ( $u, $s, $id ) = split( ":", "$urn" );

        # Set the type (folder or page)
        if ( $_->[1] =~ /scrapbook-rdf#type$/ ) {
            if ( $_->[2] eq "" ) {
                $scraps{$id}->{'id'}   = "$id";
                $scraps{$id}->{'type'} = "page";
            }
            if ( $_->[2] eq "folder" ) {
                $scraps{$id}->{'type'} = "folder";
            }
        }

        # Figure out folder hierarchy based on URI type (item 0 = parent folder, item 2 = child folder)
        if ( $_->[1] =~ /http:\/\/www.w3.org\/1999\/02\/22-rdf-syntax-ns/ ) {
            my $parent_folder = $id;
            my $urn2          = $_->[2];
            my ( $u, $s, $child_folder ) = split( ":", "$urn2" );

            # Set the immediate parent folder
            $scraps{$child_folder}->{'parent_folder_item'} = $parent_folder;

            # Set child folders - uncomment if needed for parsing
            push(
                @{ $scraps{$parent_folder}->{'child_folder_items'} },
                $child_folder
              )
              if defined($child_folder);

        }

        # Set the original source URL
        if ( $_->[1] =~ /scrapbook-rdf#source$/ ) {
            $scraps{$id}->{'source_url'} = $_->[2];
        }

        # Set the original title
        if ( $_->[1] =~ /scrapbook-rdf#title$/ ) {
            $scraps{$id}->{'label'} = substr $_->[2], 0, 30;
            $scraps{$id}->{'title'} = $_->[2];
        }

        # Set the destination URL (inside the Scrapbook folder data directory) and saved date
        if ( $_->[1] =~ /scrapbook-rdf#id$/ ) {
            $scraps{$id}->{'saved_url'} = $base_url . "/data/" . $_->[2] . "/";

            # Use regex to convert date format
            my (@dv) = $_->[2] =~ /(\d{4})(\d{2})(\d{2})/;
            $scraps{$id}->{'saved_date'} = "$dv[0]-$dv[1]-$dv[2]";
            $scraps{$id}->{'saved_year'} = $dv[0];
        }

    }

    $logger->info("Finding folder names and building index");

    foreach ( keys(%scraps) ) {

        # Get parent folder item ID to resolve folder name
        my $parent_item = $scraps{$_}->{'parent_folder_item'};
        $scraps{$_}->{'parent_folder_name'} = $scraps{$parent_item}->{'title'};
        if ( !defined( $scraps{$_}->{'parent_folder_name'} ) ) {
            $scraps{$_}->{'parent_folder_name'} = "root";
        }

        # Recursively determine all parent folders
        my $top_item = $scraps{$parent_item}->{'parent_folder_item'};
        while ( defined($top_item) ) {
            push(
                @{ $scraps{$_}->{'top_folder_names'} },
                $scraps{$top_item}->{'title'}
              )
              if defined( $scraps{$top_item}->{'title'} );
            $top_item = $scraps{$top_item}->{'parent_folder_item'};
        }

        if ( $scraps{$_}->{'type'} eq "folder" ) {
            $index{ $scraps{$_}->{'title'} } = $scraps{$_};
        }
    }

    $logger->info("Completed parsing");

}

# Prints HTML in format recognized by the jqueryFileTree plugin
sub print_tree_jquery {
    my ($folder) = @_;

    print qq{ <ul class="jqueryFileTree" style="display: none;">\n};

    $logger->debug("Calling get_folders_by_parent($folder)");
    foreach ( get_folders_by_parent($folder) ) {
        $logger->debug("folder = $_");
        print qq{ <li class="directory collapsed"><a href="#" rel="$_/">$_</a></li>\n};
    }

    $logger->debug("Calling get_items_by_folder($folder)");
    foreach ( get_items_by_folder($folder) ) {
        $logger->debug("item = $_->{'title'}");
        print qq{ <li class="file ext_html"><a href="$_->{'saved_url'}" rel="$_->{'saved_url'}">$_->{'title'}</a></li>\n};
    }

    print qq{</ul>\n};
    
     $logger->info("Completed print_tree_jquery()");

}

# Prints JSON in format recognized Simile Exhibit
sub print_exhibit_json {
    
    my $json = new JSON;
    my $json_output = "";
    
    foreach ( keys(%scraps) ) {
    
	     # Delete key since it's no longer needed for serialization          
	     delete( $scraps{$_}->{'parent_folder_item'} );
	
	    # Exclude folders
	    next if !defined($scraps{$_}->{'saved_url'});         
	    next if $scraps{$_}->{'type'} eq "folder";                
	
	    # Delete key since it's no longer needed for serialization
	    delete( $scraps{$_}->{'type'} );
	
	    my $json_text = $json->pretty->encode( $scraps{$_} );
	    chop $json_text;
	    $json_output .= $json_text . ",\n";
    
    }
    
    chop($json_output);
    chop($json_output);

    print qq{
        {
        "items" : [
              $json_output               
        ]
        }
    };
    
    $logger->info("Completed print_exhibit_json()");
    
}

sub get_rdf_last_modified() {
    
    my $ua = new LWP::UserAgent;
    my $request_head = new HTTP::Request( 'HEAD', $scrapbook_url );
    my $head = $ua->request($request_head);
    my $status = $head->{'_rc'};
    
    if ( !$head->is_success ) {
        die "Error retriving file: status = $status";
    }
    
    # Check when the file was last modified and determine if version in cache is current
    my $last_modified = $head->{'_headers'}->{'last-modified'};
    
    return ($last_modified);
    
}

print $q->header();

my $memd = new Cache::Memcached {
    'servers' => [ "127.0.0.1:11211" ],
    'debug' => 0
  };

my $rdf_last_modified = get_rdf_last_modified();
$logger->info("RDF last modified = $rdf_last_modified");
my $cache_last_modified = $memd->get("scrapboook_last_modified");
$logger->info("Cache last modified = $cache_last_modified");

if ($rdf_last_modified eq $cache_last_modified) {
    $logger->info("Items in cache, setting scraps and index");
    %scraps = %{$memd->get("scraps")};
    $logger->debug(Dumper(\%scraps));
    %index = %{$memd->get("index")};
    $logger->debug(Dumper(\%index));
}
else {
    $logger->info("Item not in cache, calling parse_rdf()");
    parse_rdf();
    $memd->set("scrapboook_last_modified", $rdf_last_modified);
    $memd->set("scraps", \%scraps);
    $memd->set("index", \%index);
}

my $folder = $q->param('dir');
# Must have a trailing slash on the end of the rel attribute or jquery tree builder won't render
$folder =~ s/\///g;
# Convert spaces
$folder =~ s/%20/ /g;

$logger->info("folder = $folder");
#$logger->debug("ENV = " . Dumper(\%ENV));

if ($ENV{'HTTP_REFERER'} =~ /tree/) {
    print_tree_jquery($folder)
}
if ($ENV{'HTTP_REFERER'} =~ /exhibit/) {
    print_exhibit_json();
}


